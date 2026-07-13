"""溯源查询层（v1.28 落地，G20 ①确定性证据链——事件流失败链回溯）。

design.md G20：
- 触发（VerifyFailed / Error / 用户"为什么挂"）→ 确定性证据链（图谱调用链 BFS
  + **事件流失败链回溯** + FixPacket 机读契约 + YAGNI 静态分析外环）
- 结构性防误报：失败/疑问才溯源，不做全库扫描体检
- 溯源报告三阶段定位（search/read/edit，TRAJEVAL 口径）→ 证据集 → 修复指令

本模块 = 溯源查询层（失败事件过滤 + 失败链回溯 + 证据集组装），**纯确定性零 LLM**。
LLM 归因假设在 attribution.py（T-5），本层只做"失败→根因证据链"的确定性部分。

范式声明：业务逻辑层 OOP（ProjectionUnit + 纯查询类，遵守 v1.26 投影纪律）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..protocol import Event, EventType
from ..protocol.events import Error as ErrorEvent, Retry, Rollback, RootCauseReport

# 失败/疑问触发溯源的事件类型（结构性防误报：仅这些事件触发，非全库扫描）
FAILURE_EVENT_TYPES = {
    EventType.ERROR,
    EventType.RETRY,
    EventType.ROLLBACK,
    EventType.ROOT_CAUSE_REPORT,
}


class FailureIndexUnit:
    """失败事件索引投影单元（v1.26 投影纪律：框架驱动、领域计算）。

    持续折叠失败事件 → 按 session 索引，幂等（每事件只落一次），
    同引用即无工作（无关事件返回同一引用）。
    """

    key = "failure_index"
    stateVersion = 1

    def init(self) -> dict:
        return {"by_session": {}, "by_id": {}}

    def apply(self, state: dict, event: Event) -> dict:
        if event.type not in FAILURE_EVENT_TYPES:
            return state  # 同引用即无工作
        session_id = getattr(event, "session_id", "")
        entry = {
            "id": event.id,
            "type": event.type.value,
            "ts": event.ts,
            "session_id": session_id,
            "message": getattr(event, "message", ""),
            "error_type": getattr(event, "error_type", ""),
            "failure_stage": getattr(event, "failure_stage", None),
            "related_tool": getattr(event, "related_tool", None),
            "retry_id": getattr(event, "retry_id", None),
            "checkpoint_id": getattr(event, "checkpoint_id", None),
            "reason": getattr(event, "reason", ""),
        }
        # 幂等：同一事件 id 不重复入索引
        by_id = dict(state["by_id"])
        if event.id in by_id:
            return state
        by_id[event.id] = entry
        by_session = dict(state["by_session"])
        by_session.setdefault(session_id, []).append(entry)
        by_session[session_id].sort(key=lambda e: e["id"])
        return {"by_session": by_session, "by_id": by_id}

    def view(self, state: dict) -> dict:
        return {
            "count": len(state["by_id"]),
            "by_session": {k: v[-50:] for k, v in state["by_session"].items()},
        }


@dataclass
class FailureChain:
    """失败链（同 session 的失败事件序列 + 关联上下文事件）。"""

    session_id: str
    anchor_event_id: int = 0  # 触发溯源的事件 id
    failures: list[dict] = field(default_factory=list)  # 失败事件（时间序）
    related_events: list[dict] = field(default_factory=list)  # 关联上下文（工具调用/审批/回滚）
    trace_records: list[dict] = field(default_factory=list)  # 执行轨迹（沙箱不可知）
    yagni_findings: list[dict] = field(default_factory=list)  # YAGNI 外环（注入）
    fix_packets: list[dict] = field(default_factory=list)  # FixPacket 机读契约（注入）

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "anchor_event_id": self.anchor_event_id,
            "failures": self.failures,
            "related_events": self.related_events,
            "trace_records": self.trace_records,
            "yagni_findings": self.yagni_findings,
            "fix_packets": self.fix_packets,
        }


# 与失败链关联的上下文事件类型（工具调用/审批/快照/分类器判决——失败发生前后的证据）
_CONTEXT_EVENT_TYPES = {
    EventType.EXEC_APPROVAL_REQUEST,
    EventType.ITEM_COMPLETED,
    EventType.SNAPSHOT_CREATED,
    EventType.CLASSIFIER_VERDICT,
    EventType.TRACE_RECORD,
    EventType.CONDENSATION,
}
# 溯源查询的窗口（失败事件前后各多少条事件算"相关上下文"）
_CONTEXT_WINDOW = 20


class RootCauseQuerier:
    """溯源查询层：失败事件过滤 + 失败链回溯 + 证据集组装。

    纯确定性零 LLM——输入 EventLog + 可选注入（图谱影响面/yagni findings/fix packets），
    输出 FailureChain 证据集，供 LLM 归因假设（T-5）消费。
    """

    def __init__(self, event_log, index: Optional[FailureIndexUnit] = None) -> None:
        self.event_log = event_log
        self._index = index or FailureIndexUnit()

    # ---------- 失败事件过滤（按 session/类型） ----------

    def failure_events(self, session_id: Optional[str] = None, limit: int = 100) -> list[dict]:
        """过滤失败事件（Error/Retry/Rollback），可按 session 过滤，时间序。"""
        events = self.event_log.read_all()
        result = []
        for ev in events:
            if ev.type not in FAILURE_EVENT_TYPES:
                continue
            if session_id is not None and getattr(ev, "session_id", "") != session_id:
                continue
            result.append(self._failure_entry(ev))
            if len(result) >= limit:
                break
        return result

    # ---------- 失败链回溯（确定性证据链核心） ----------

    def trace_failure_chain(
        self,
        anchor_event_id: int,
        *,
        session_id: Optional[str] = None,
        yagni_findings: Optional[list[dict]] = None,
        fix_packets: Optional[list[dict]] = None,
        impact_scope: Optional[list[dict]] = None,
    ) -> FailureChain:
        """从锚点失败事件回溯失败链 + 组装关联证据集。

        - failures：同 session 的失败事件序列（含锚点，时间序）
        - related_events：锚点前后窗口内的工具调用/审批/快照事件（证据链上下文）
        - trace_records：同 session 的执行轨迹
        - 注入槽：yagni_findings（YAGNI 外环）/ fix_packets（FixPacket 契约）/
          impact_scope（图谱 BFS 影响面）——由上层调用方注入
        """
        all_events = self.event_log.read_all()
        anchor = next((ev for ev in all_events if ev.id == anchor_event_id), None)
        sid = session_id or (getattr(anchor, "session_id", "") if anchor else "")
        # 无失败事件锚点（anchor_event_id=0 / 不存在）→ 空失败链（安全生成空报告）
        if anchor is None:
            return FailureChain(session_id=sid, anchor_event_id=anchor_event_id)

        # 1. 失败事件序列（同 session，含锚点，时间序）
        failures = [
            self._failure_entry(ev)
            for ev in all_events
            if ev.type in FAILURE_EVENT_TYPES
            and (not sid or getattr(ev, "session_id", "") == sid)
            and ev.id <= anchor_event_id
        ]
        # 2. 锚点前后窗口内的上下文事件（工具调用/审批/快照/分类器判决）
        anchor_idx = next((i for i, ev in enumerate(all_events) if ev.id == anchor_event_id), 0)
        window = all_events[max(0, anchor_idx - _CONTEXT_WINDOW) : anchor_idx + _CONTEXT_WINDOW]
        related = [
            self._context_entry(ev)
            for ev in window
            if ev.type in _CONTEXT_EVENT_TYPES and (not sid or getattr(ev, "session_id", "") == sid)
        ]
        # 3. 同 session 执行轨迹（沙箱不可知）
        trace = [
            self._trace_entry(ev)
            for ev in all_events
            if ev.type == EventType.TRACE_RECORD and (not sid or getattr(ev, "session_id", "") == sid)
        ]
        chain = FailureChain(
            session_id=sid,
            anchor_event_id=anchor_event_id,
            failures=failures[-50:],
            related_events=related[-50:],
            trace_records=trace[-50:],
            yagni_findings=yagni_findings or [],
            fix_packets=fix_packets or [],
        )
        if impact_scope:
            chain.related_events.extend(
                {"kind": "impact_scope", "entity": e} for e in impact_scope
            )
        return chain

    # ---------- 溯源报告组装（③ 溯源报告：阶段定位 + 证据集 + 修复指令） ----------

    def build_report_event(
        self,
        *,
        report_id: str,
        trigger: str,
        trigger_event_id: int,
        chain: FailureChain,
        attributions: Optional[list[dict]] = None,
        stages: Optional[list[dict]] = None,
        fix_instructions: Optional[list[str]] = None,
        status: str = "completed",
        session_id: str = "",
    ) -> RootCauseReport:
        """组装 RootCauseReport 事件（G20 ⑤沉淀：溯源即事件，可审计可回放）。

        stages = TRAJEVAL 三阶段定位（search/read/edit），由上层归因/报告层填充；
        fix_instructions = 修复指令（FixPacket 消费闭环，机读可消费）。
        """
        return RootCauseReport(
            id=self.event_log.next_event_id(),
            session_id=session_id or chain.session_id,
            report_id=report_id,
            trigger=trigger,
            trigger_event_id=trigger_event_id,
            status=status,
            ts=time.time(),
            evidence={
                "call_chain": [e.get("entity") for e in chain.related_events if e.get("kind") == "impact_scope"],
                "failure_chain": chain.failures,
                "related_events": [e for e in chain.related_events if e.get("kind") != "impact_scope"],
                "trace_records": chain.trace_records,
                "yagni_findings": chain.yagni_findings,
                "fix_packets": chain.fix_packets,
            },
            attributions=attributions or [],
            stages=stages or [],
            fix_instructions=fix_instructions or [],
        )

    # ---------- 内部转换 ----------

    @staticmethod
    def _failure_entry(ev: Event) -> dict:
        return {
            "id": ev.id,
            "type": ev.type.value,
            "ts": ev.ts,
            "session_id": getattr(ev, "session_id", ""),
            "message": getattr(ev, "message", ""),
            "error_type": getattr(ev, "error_type", ""),
            "failure_stage": getattr(ev, "failure_stage", None),
            "related_tool": getattr(ev, "related_tool", None),
            "reason": getattr(ev, "reason", ""),
        }

    @staticmethod
    def _context_entry(ev: Event) -> dict:
        kind = {
            EventType.EXEC_APPROVAL_REQUEST: "approval",
            EventType.ITEM_COMPLETED: "tool_result",
            EventType.SNAPSHOT_CREATED: "snapshot",
            EventType.CLASSIFIER_VERDICT: "classifier",
            EventType.TRACE_RECORD: "trace",
            EventType.CONDENSATION: "condensation",
        }.get(ev.type, ev.type.value)
        return {
            "id": ev.id,
            "kind": kind,
            "ts": ev.ts,
            "tool_name": getattr(ev, "tool_name", None),
            "item_type": getattr(ev, "item_type", None),
            "decision": getattr(ev, "decision", None),
            "reason": getattr(ev, "reason", None),
        }

    @staticmethod
    def _trace_entry(ev: Event) -> dict:
        return {
            "id": ev.id,
            "ts": ev.ts,
            "executor": getattr(ev, "executor", ""),
            "command": getattr(ev, "command", ""),
            "exit_code": getattr(ev, "exit_code", None),
            "duration_ms": getattr(ev, "duration_ms", 0.0),
        }
