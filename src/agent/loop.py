"""Agent Loop 主循环。

架构链路：
    Op（UserTurnStart/ApprovalResponse/Cancel/Compact）
        → Loop.step() 消费
        → StateMachine 状态流转
        → 调用 LLM（可注入）+ 工具（可注入）
        → 对外 Event 写入 EventLog（事实源）

设计要点：
- 内核只通过协议与界面通信（G3）：Loop 不直接发 WebSocket，只写 EventLog
- 幂等：Op 带 op_id，重复提交不重复执行
- 审批即事件：危险工具调用 → 发 ExecApprovalRequest 留库 → WAITING_APPROVAL → 用户 ApprovalResponse 后隐式执行
- LLM/工具为协议接口：Phase 1 Mock 跑通，Phase 4/5 接真实 Provider + ToolRegistry
"""

from __future__ import annotations

import time
from typing import Optional, Protocol

from ..protocol import (
    AgentMessageContentDelta,
    ApprovalResponse,
    ClassifierVerdict,
    Compact,
    ErrorEvent,
    Event,
    ExecApprovalRequest,
    ItemCompleted,
    Op,
    OpType,
    TurnCancelled,
    TurnStarted,
    UserTurnCancel,
    UserTurnStart,
)
from ..storage import EventLog
from .events import InternalEvent, InternalEventType, TerminationReason
from .state_machine import AgentState, StateMachine


class LLMProtocol(Protocol):
    """LLM 接口（Phase 1 Mock / Phase 5 真实 Provider 适配）。"""

    def generate(self, messages: list[dict], *, role: str = "editor") -> str: ...


class ToolProtocol(Protocol):
    """工具接口（Phase 1 Mock / Phase 3 ToolRegistry 适配）。"""

    def call(self, name: str, args: dict) -> dict: ...

    def list_tools(self) -> list[dict]: ...


class EventIdGenerator:
    """全局递增 Event id（跨进程唯一：进程前缀 + 计数器）。

    v1.13 修复：start 参数从磁盘已有最大 id 续起（默认 0）。
    旧实现每次进程启动从 1 重新计数，与 JSONL 历史事件 id 冲突，
    破坏断线重连 cursor 增量补发（Event id 是游标基准，见 protocol/events.py）。
    """

    def __init__(self, prefix: str = "e", start: int = 0) -> None:
        self._prefix = prefix
        self._seq = start

    def next(self) -> int:
        self._seq += 1
        return self._seq


class AgentLoop:
    """Agent Loop 主循环：Op 入队 → 状态机 → Event 出库。"""

    def __init__(
        self,
        event_log: EventLog,
        llm: Optional[LLMProtocol] = None,
        tools: Optional[ToolProtocol] = None,
        *,
        max_iterations: int = 500,
        session_id: str = "default",
        mode: str = "act",
        event_id_gen: Optional[EventIdGenerator] = None,
    ) -> None:
        self.event_log = event_log
        self.llm = llm
        self.tools = tools
        self.session_id = session_id
        self.mode = mode
        self.state_machine = StateMachine(max_iterations=max_iterations)
        # P1-6 修复：事件 id 统一走 EventLog.next_event_id()（flock 原子分配，跨进程唯一）。
        # event_id_gen 参数保留仅为向后兼容（旧调用方显式传入时不再影响 id 生成）。
        self._event_ids = event_id_gen or EventIdGenerator(start=event_log.last_id())
        self._pending_ops: list[Op] = []  # 提示排队（对标 pi-web prompt queuing）
        self._processed_op_ids: set[str] = set()  # 幂等集合
        self._waiting_approval: Optional[ExecApprovalRequest] = None
        self._turn_index = 0
        self._message_index = 0
        self._cancelled = False
        # G18 自动安全分类器（v1.23 落地）：None = 使用默认分类器（无 LLM 规则降级）
        self._classifier: Optional[object] = None
        self._last_user_message = ""
        # v1.23 落地③/④ 集成（可注入，None = 关闭）：调度/预算/按 Op 路由/策略
        self._scheduler = None       # LoopScheduler：调度触发 → UserTurnStart 入队
        self._budget = None          # LoopBudget：每 Op 记账 + 超限熔断
        self._op_router = None       # OpRouter：按工具名分派档位（成本归因）
        self._policy_engine = None   # PolicyEngine：工具执行前策略判定
        self._last_policy_reason = ""
        # v1.28 G20 诊断回喂（可注入，None = 关闭）：失败后下一轮修复自动注入溯源诊断
        self._root_cause_analyzer = None    # RootCauseAnalyzer：失败 → 溯源报告 + 诊断载荷
        self._pending_diagnostic: Optional[dict] = None  # 待注入下一轮修复的诊断信号

    def set_scheduler(self, scheduler: object) -> None:
        """注入 LoopScheduler（调度触发接入）。"""
        self._scheduler = scheduler

    def set_budget(self, budget: object) -> None:
        """注入 LoopBudget（token 硬预算记账 + 熔断）。"""
        self._budget = budget

    def set_op_router(self, op_router: object) -> None:
        """注入 OpRouter（按 Op 分派，成本归因 + 合规审计）。"""
        self._op_router = op_router

    def set_policy_engine(self, policy_engine: object) -> None:
        """注入 PolicyEngine（策略即代码：工具执行前 deny/require_approval 判定）。"""
        self._policy_engine = policy_engine

    def set_root_cause_analyzer(self, analyzer: object) -> None:
        """注入 RootCauseAnalyzer（v1.28 G20）：失败 → 溯源报告 → 诊断回喂。

        接线后：Error 事件发生时自动触发溯源，报告注入下一轮修复上下文
        （CodeTracer 反思回放思想——"正确时机提供诊断信号远比多给重试机会有价值"）。
        """
        self._root_cause_analyzer = analyzer

    def set_llm(self, llm: object) -> None:
        """v1.30 T-11c：热注入真实 LLM（ModelRouterAdapter），运行时替换 Mock。

        场景：init_cockpit 时 LLM 凭据未就绪（先启动服务再配凭据），或运行中切换模型。
        None = 恢复 Mock（不阻断内核）。
        """
        self.llm = llm

    def set_evolution_engine(self, engine: object) -> None:
        """v1.31 G22：注入自进化引擎（EvolutionEngine）。

        进化闭环：Observe→Analyze→Improve→Verify→Persist
        五个作用目标共享同一闭环，策略层控制进化边界。
        """
        self._evolution_engine = engine

    # ---------- Op 入口 ----------

    def _next_event_id(self) -> int:
        """事件 id 原子分配（P1-6 修复）：跨进程唯一，走 EventLog 共享计数器。"""
        return self.event_log.next_event_id()

    def enqueue_op(self, op: Op) -> None:
        """Op 入队（agent 忙时排队，跑完自动处理——pi-web prompt queuing）。"""
        if op.op_id in self._processed_op_ids:
            return  # 幂等：重复提交不重复执行
        self._pending_ops.append(op)

    def step(self) -> Optional[Event]:
        """消费一个 Op 并推进状态机，返回本轮产出的最后一个对外 Event（None=无事可做）。"""
        # 等待审批时优先消费 ApprovalResponse Op
        if self.state_machine.state == AgentState.WAITING_APPROVAL:
            idx = next((i for i, o in enumerate(self._pending_ops) if isinstance(o, ApprovalResponse)), None)
            if idx is not None:
                return self._start_new_turn(self._pending_ops.pop(idx))
            return None

        if self.state_machine.is_terminal():
            if self._pending_ops:
                return self._start_new_turn(self._pending_ops.pop(0))
            return None

        if self.state_machine.state == AgentState.IDLE:
            if self._pending_ops:
                return self._start_new_turn(self._pending_ops.pop(0))
            return None

        # 非空闲：按当前状态推进
        return self._advance()

    def run_until_idle(self, max_steps: int = 100) -> list[Event]:
        """驱动循环直到空闲/终止（headless run 模式用）。

        终止条件：终态且无排队 Op / 等待用户且无对应响应排队（WAITING_APPROVAL / ASKING_USER）。
        """
        emitted: list[Event] = []
        for _ in range(max_steps):
            if self.state_machine.is_terminal() and not self._pending_ops:
                break
            if self.state_machine.state == AgentState.IDLE and not self._pending_ops:
                break
            if self.state_machine.state in (AgentState.WAITING_APPROVAL, AgentState.ASKING_USER):
                # 有对应响应 Op 排队则继续，否则等待用户
                has_response = any(
                    isinstance(o, (ApprovalResponse, UserTurnCancel)) for o in self._pending_ops
                )
                if not has_response:
                    break
            ev = self.step()
            if ev is not None:
                emitted.append(ev)
        return emitted

    # ---------- 状态推进 ----------

    def _start_new_turn(self, op: Op) -> Optional[Event]:
        """开始新一轮（UserTurnStart 或恢复处理排队的下一 Op）。

        ApprovalResponse 是当前轮的延续，不递增 turn_index。
        """
        self._processed_op_ids.add(op.op_id)
        if not isinstance(op, ApprovalResponse):
            self._turn_index += 1
            self._message_index = 0
            self._cancelled = False
        # 终态后重置状态机（FINISHED/CANCELLED/ERROR → IDLE，允许新一轮）
        if self.state_machine.is_terminal():
            self.state_machine.force_set(AgentState.IDLE)

        if isinstance(op, UserTurnStart):
            self.mode = op.mode
            self._last_user_message = op.content
            turn_started = TurnStarted(
                id=self._next_event_id(),
                session_id=self.session_id,
                mode=op.mode,
                turn_index=self._turn_index,
                op_id=op.op_id,
                ts=time.time(),
            )
            self.event_log.append(turn_started)
            self.state_machine.transition(AgentState.PLANNING)
            # 内部事件：用户消息 → 规划
            self._handle_internal(
                InternalEvent(type=InternalEventType.USER_MESSAGE, content=op.content, turn_index=self._turn_index)
            )
            return turn_started
        elif isinstance(op, UserTurnCancel):
            self._cancelled = True
            ev = TurnCancelled(id=self._next_event_id(), session_id=self.session_id, reason=op.reason, ts=time.time())
            self.event_log.append(ev)
            self.state_machine.force_set(AgentState.CANCELLED, TerminationReason.NEEDS_CLARIFICATION)
            return ev
        elif isinstance(op, ApprovalResponse):
            return self._handle_approval(op)
        elif isinstance(op, Compact):
            # Compact：触发上下文压缩（Phase 3 实现压缩器，此处发完成事件占位）
            ev = ItemCompleted(
                id=self._next_event_id(),
                session_id=self.session_id,
                item_type="turn_summary",
                item_id=f"compact-{self._turn_index}",
                content={"action": "compact_requested"},
                ts=time.time(),
            )
            self.event_log.append(ev)
            return ev
        return None

    def _advance(self) -> Optional[Event]:
        """非空闲状态推进一步。"""
        state = self.state_machine.state

        if state == AgentState.PLANNING:
            return self._do_planning()
        if state == AgentState.WAITING_TOOL:
            return self._do_waiting_tool()
        if state == AgentState.EXECUTING:
            return self._do_executing()
        if state == AgentState.REFLECTING:
            return self._do_reflecting()
        if state == AgentState.WAITING_APPROVAL:
            # 等待用户审批：无事可做，直到 ApprovalResponse
            return None
        if state == AgentState.ASKING_USER:
            return self._do_asking_user()
        return None

    # ---------- 阶段实现（Phase 1 可跑通的最小闭环） ----------

    def _do_planning(self) -> Optional[Event]:
        """规划阶段：LLM 产出计划（Phase 1 简化：单步计划）。

        v1.28 G20 诊断回喂：上一轮失败产出的溯源诊断注入本轮规划上下文
        （CodeTracer 反思回放——正确时机提供诊断信号远比多给重试机会有价值）。
        """
        if self.llm is not None:
            # 诊断回喂注入（有失败诊断 + 有 LLM 时才注入）
            plan_content = f"[PLAN] 会话:{self.session_id} turn:{self._turn_index}"
            if self._pending_diagnostic:
                frag = self._pending_diagnostic.get("prompt_fragment", "")
                if frag:
                    plan_content = f"{plan_content}\n{self._pending_diagnostic.get('trigger', 'diagnostic')}\n{frag}"
                self._pending_diagnostic = None  # 注入一次即消费
            plan = self.llm.generate(
                [{"role": "user", "content": plan_content}],
                role="architect",
            )
            self._emit_message(plan)
            self._handle_internal(
                InternalEvent(type=InternalEventType.PLAN, content=plan, turn_index=self._turn_index)
            )
        # 有工具则进入工具决策点，否则直接完成
        if self.tools is not None:
            self.state_machine.transition(AgentState.WAITING_TOOL)
        else:
            self.state_machine.transition(AgentState.FINISHED, meta={"reason": "no_tools"})
            self.state_machine.termination_reason = TerminationReason.COMPLETED
        return None

    def _do_waiting_tool(self) -> Optional[Event]:
        """工具决策点：取下一个待执行工具，评估风险（审批即事件）。"""
        if self.tools is None:
            self.state_machine.transition(AgentState.FINISHED, meta={"reason": "no_tools"})
            self.state_machine.termination_reason = TerminationReason.COMPLETED
            return None

        tools = self.tools.list_tools()
        if not tools:
            # 无待执行工具 → 完成
            self.state_machine.transition(AgentState.FINISHED, meta={"reason": "all_tools_done"})
            self.state_machine.termination_reason = TerminationReason.COMPLETED
            return self._emit_summary()

        # Phase 1 简化：只调第一个工具（Phase 3 起由 LLM 决策调用序列）
        tool = tools[0]
        name, args = tool["name"], tool.get("args", {})

        # v1.23 落地③：策略即代码（PolicyEngine）——企业管理面先于运行时防御。
        # deny → 硬拦；require_approval → 升级人工审批（与分类器 escalate 同路径）
        policy_decision = self._apply_policy(name, args)
        if policy_decision is not None:
            if policy_decision == "deny":
                self._emit_message(f"策略拒绝 {name}: {self._last_policy_reason}")
                self.state_machine.transition(AgentState.FINISHED, meta={"reason": "policy_denied"})
                self.state_machine.termination_reason = TerminationReason.NEEDS_CLARIFICATION
                return None
            if policy_decision == "require_approval":
                return self._emit_approval(name, args, risk_level="red")
            # require_judge → 放行（judge 验证在 verify 层），继续

        # v1.23 落地③：按 Op 分派记账（成本归因 + 合规审计）
        self._route_op(name)

        # G18 自动安全分类器（v1.23 落地）：Tier3 动作过分类器，判决进事件流。
        # 三级处置：block（硬拦，不执行）/ escalate（升级人工审批）/ allow（放行执行）
        verdict = self._classify_tool(name, args)
        if verdict is not None:
            if verdict["decision"] == "block":
                # 硬拦：拒绝带原因（不静默吞），agent 可看到理由
                self._emit_message(f"安全分类器拦截 {name}: {verdict['reason']}")
                self.state_machine.transition(AgentState.FINISHED, meta={"reason": "classifier_blocked"})
                self.state_machine.termination_reason = TerminationReason.NEEDS_CLARIFICATION
                return None
            if verdict["decision"] == "escalate":
                # 升级人工审批：发 ExecApprovalRequest（收件箱/审批中心展示）
                approval = ExecApprovalRequest(
                    id=self._next_event_id(),
                    session_id=self.session_id,
                    approval_id=f"appr-{self._turn_index}-{self.state_machine.steps}",
                    tool_name=name,
                    description=self._describe_tool(name, args),
                    command=args.get("command", ""),
                    risk_level="red",
                    diff_preview=args.get("diff_preview"),
                    ts=time.time(),
                )
                self.event_log.append(approval)
                self._waiting_approval = approval
                self.state_machine.transition(AgentState.WAITING_APPROVAL)
                return approval
            # allow → 低风险进入执行
            self.state_machine.transition(AgentState.EXECUTING)
            return None

        # 回退：分类器未启用时走既有风险评估（审批即事件）
        risk = self._assess_risk(name, args)
        if risk in ("red", "yellow"):
            approval = ExecApprovalRequest(
                id=self._next_event_id(),
                session_id=self.session_id,
                approval_id=f"appr-{self._turn_index}-{self.state_machine.steps}",
                tool_name=name,
                description=self._describe_tool(name, args),
                command=args.get("command", ""),
                risk_level=risk,
                diff_preview=args.get("diff_preview"),
                ts=time.time(),
            )
            self.event_log.append(approval)
            self._waiting_approval = approval
            self.state_machine.transition(AgentState.WAITING_APPROVAL)
            return approval

        # 低风险 → 进入执行
        self.state_machine.transition(AgentState.EXECUTING)
        return None

    def _do_executing(self) -> Optional[Event]:
        """执行阶段：调用工具（Phase 1 简化：逐个调用，全部完成后回合收尾）。"""
        if self.tools is None:
            self.state_machine.transition(AgentState.FINISHED, meta={"reason": "no_tools"})
            self.state_machine.termination_reason = TerminationReason.COMPLETED
            return None

        tools = self.tools.list_tools()
        if not tools:
            # 无待执行工具 → 完成
            self.state_machine.transition(AgentState.FINISHED, meta={"reason": "all_tools_done"})
            self.state_machine.termination_reason = TerminationReason.COMPLETED
            return self._emit_summary()

        tool = tools[0]
        name, args = tool["name"], tool.get("args", {})
        result = self.tools.call(name, args)
        self._handle_internal(
            InternalEvent(type=InternalEventType.TOOL_RESULT, content=result, turn_index=self._turn_index)
        )
        item = ItemCompleted(
            id=self._next_event_id(),
            session_id=self.session_id,
            item_type="tool_result",
            item_id=f"{name}-{self.state_machine.steps}",
            content=result,
            ts=time.time(),
        )
        self.event_log.append(item)
        # 工具结果已消费（Phase 1 简化），本轮收尾
        self.state_machine.transition(AgentState.FINISHED, meta={"reason": "phase1_single_tool"})
        self.state_machine.termination_reason = TerminationReason.COMPLETED
        return item

    def _handle_approval(self, op: ApprovalResponse) -> Optional[Event]:
        """处理审批响应：批准 → 执行工具；拒绝 → 反思；edit → 用修改后的命令执行。"""
        if self._waiting_approval is None or op.approval_id != self._waiting_approval.approval_id:
            return None  # 幂等/过期：忽略

        approval = self._waiting_approval
        self._waiting_approval = None
        name = approval.tool_name
        args = {"command": approval.command}

        if op.decision == "reject":
            self.state_machine.transition(AgentState.REFLECTING)
            self._emit_message(f"工具 {name} 被拒绝，重新规划。")
            return None
        if op.decision == "edit" and op.edited_command:
            args["command"] = op.edited_command

        # 批准 → 隐式执行
        self.state_machine.transition(AgentState.EXECUTING)
        if self.tools is None:
            self.state_machine.transition(AgentState.FINISHED)
            return None
        result = self.tools.call(name, args)
        self._handle_internal(
            InternalEvent(type=InternalEventType.TOOL_RESULT, content=result, turn_index=self._turn_index)
        )
        item = ItemCompleted(
            id=self._next_event_id(),
            session_id=self.session_id,
            item_type="tool_result",
            item_id=f"{name}-approved",
            content=result,
            ts=time.time(),
        )
        self.event_log.append(item)
        self.state_machine.transition(AgentState.FINISHED, meta={"reason": "approval_executed"})
        self.state_machine.termination_reason = TerminationReason.COMPLETED
        return item

    def _do_reflecting(self) -> Optional[Event]:
        """反思阶段：Phase 1 简化——失败后直接完成。"""
        self.state_machine.transition(AgentState.FINISHED)
        self.state_machine.termination_reason = TerminationReason.NEEDS_CLARIFICATION
        self._emit_message("任务未能完成，需要人工介入。")
        return None

    def _do_asking_user(self) -> Optional[Event]:
        self.state_machine.transition(AgentState.FINISHED)
        self.state_machine.termination_reason = TerminationReason.NEEDS_CLARIFICATION
        return None

    # ---------- 内部工具 ----------

    def _handle_internal(self, ev: InternalEvent) -> None:
        """内部事件处理（Phase 1 最小化：仅记录，后续扩展钩子）。"""
        if ev.type == InternalEventType.ERROR:
            err = ErrorEvent(
                id=self._next_event_id(),
                session_id=self.session_id,
                message=str(ev.content),
                error_type=ev.meta.get("error_type", "unknown"),
                failure_stage=ev.meta.get("failure_stage"),   # v1.28：TRAJEVAL 三阶段口径
                related_tool=ev.meta.get("related_tool"),     # v1.28：溯源过滤
                ts=time.time(),
            )
            self.event_log.append(err)
            # v1.28 G20 诊断回喂：失败 → 溯源报告 → 注入下一轮修复（CodeTracer 反思回放）
            if self._root_cause_analyzer is not None:
                try:
                    _, feed = self._root_cause_analyzer.analyze(
                        trigger="error",
                        trigger_event_id=err.id,
                        session_id=self.session_id,
                    )
                    self._pending_diagnostic = feed
                except Exception:
                    self._pending_diagnostic = None  # 溯源失败不阻断主流程
            self.state_machine.transition(AgentState.ERROR)
            self.state_machine.termination_reason = TerminationReason.EXCEPTION

    def _emit_message(self, text: str) -> AgentMessageContentDelta:
        """发送 Agent 消息（流式增量，一次完整发送）。"""
        delta = AgentMessageContentDelta(
            id=self._next_event_id(),
            session_id=self.session_id,
            message_index=self._message_index,
            delta=text,
            complete=True,
            ts=time.time(),
        )
        self.event_log.append(delta)
        self._message_index += 1
        return delta

    def _emit_summary(self) -> ItemCompleted:
        """回合总结事件。"""
        summary = ItemCompleted(
            id=self._next_event_id(),
            session_id=self.session_id,
            item_type="turn_summary",
            item_id=f"turn-{self._turn_index}",
            content={"status": "completed", "steps": self.state_machine.steps},
            ts=time.time(),
        )
        self.event_log.append(summary)
        return summary

    def set_classifier(self, classifier: object) -> None:
        """注入自动安全分类器（G18）。None 恢复默认（走静态风险评估）。"""
        self._classifier = classifier

    def _apply_policy(self, name: str, args: dict) -> Optional[str]:
        """v1.23 落地③：策略即代码（PolicyEngine）判定。

        返回 None = 策略放行；"deny"/"require_approval"/"require_judge" = 策略处置。
        未注入策略引擎 → None（不拦截）。
        """
        if self._policy_engine is None:
            return None
        try:
            resource = str(args.get("command") or args.get("path") or args.get("file_path") or "*")
            result = self._policy_engine.evaluate(name, resource)
            self._last_policy_reason = result.get("reason", "")
            decision = result.get("decision", "allow")
            return None if decision == "allow" else decision
        except Exception:
            return None  # 策略引擎异常 → 不阻断（fail-open 由分类器兜底）

    def _route_op(self, name: str) -> None:
        """v1.23 落地③：按 Op 分派记账（成本归因 + 合规审计）。"""
        if self._op_router is None:
            return
        try:
            self._op_router.route(name)
        except Exception:
            pass

    def _emit_approval(self, name: str, args: dict, risk_level: str = "red") -> ExecApprovalRequest:
        """构造审批请求事件（策略 require_approval / 分类器 escalate 共用）。"""
        approval = ExecApprovalRequest(
            id=self._next_event_id(),
            session_id=self.session_id,
            approval_id=f"appr-{self._turn_index}-{self.state_machine.steps}",
            tool_name=name,
            description=self._describe_tool(name, args),
            command=args.get("command", ""),
            risk_level=risk_level,
            diff_preview=args.get("diff_preview"),
            ts=time.time(),
        )
        self.event_log.append(approval)
        self._waiting_approval = approval
        self.state_machine.transition(AgentState.WAITING_APPROVAL)
        return approval

    def _classify_tool(self, name: str, args: dict) -> Optional[dict]:
        """G18 自动安全分类（v1.23 落地）：Tier3 动作过分类器，判决作为 ClassifierVerdict 事件落盘。

        返回 None = 分类器未启用（走既有审批流程）。
        返回 dict = {decision: allow/block/escalate, reason, ...}（allow 已包含）
        """
        if self._classifier is None:
            return None
        from ..security import ClassifierInput

        try:
            inp = ClassifierInput(
                tool_name=name,
                args=args,
                user_message=self._last_user_message,
                session_id=self.session_id,
                op_id=getattr(self, "_current_op_id", ""),
            )
            verdict = self._classifier.classify(inp)
        except Exception as e:
            # 分类器异常 → 保守 escalate（fail-closed 不 fail-open）
            self.event_log.append(
                ClassifierVerdict(
                    id=self._next_event_id(),
                    session_id=self.session_id,
                    tool_name=name,
                    command=str(args.get("command", "")),
                    decision="escalate",
                    reason=f"分类器异常 fail-closed: {e}",
                    tier=3,
                    confidence=0.0,
                    stage="fallback",
                    ts=time.time(),
                )
            )
            return {"decision": "escalate", "reason": f"分类器异常 fail-closed: {e}"}
        # 审批即事件：判决 100% 落盘可审计
        self.event_log.append(
            ClassifierVerdict(
                id=self._next_event_id(),
                session_id=self.session_id,
                tool_name=name,
                command=str(args.get("command", "")),
                decision=verdict.decision,
                reason=verdict.reason,
                tier=verdict.tier,
                confidence=verdict.confidence,
                stage=verdict.stage,
                suggested_alternative=verdict.suggested_alternative,
                ts=time.time(),
            )
        )
        return {
            "decision": verdict.decision,
            "reason": verdict.reason,
            "tier": verdict.tier,
            "confidence": verdict.confidence,
            "stage": verdict.stage,
            "suggested_alternative": verdict.suggested_alternative,
        }

    @staticmethod
    def _assess_risk(name: str, args: dict) -> str:
        """风险等级评估（Phase 2 由 security/guard.py 接管，此处静态白名单）。"""
        if name in ("Bash",):
            cmd = str(args.get("command", ""))
            dangerous = any(k in cmd for k in ("rm -rf", "sudo", "mkfs", "dd ", ":(){", "> /dev/sda"))
            return "red" if dangerous else "yellow"
        if name in ("Write", "Edit"):
            return "yellow"
        return "green"

    @staticmethod
    def _describe_tool(name: str, args: dict) -> str:
        if name == "Bash":
            return f"执行命令: {args.get('command', '')}"
        if name in ("Write", "Edit"):
            return f"{name} 修改文件: {args.get('path', args.get('file_path', '?'))}"
        return f"调用工具 {name}"
