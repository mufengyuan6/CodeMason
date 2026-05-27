"""Phase 1 测试：状态机 + Agent Loop 主循环。"""

import time

import pytest

from src.agent import (
    AgentLoop,
    AgentState,
    EventIdGenerator,
    StateMachine,
    StateMachineError,
    TerminationReason,
)
from src.protocol.events import (
    ExecApprovalRequest,
    ItemCompleted,
    TurnCancelled,
    TurnStarted,
)
from src.protocol.ops import ApprovalResponse, UserTurnStart
from src.storage import EventLog


class MockLLM:
    def __init__(self, reply: str = "计划执行") -> None:
        self.reply = reply
        self.calls = []

    def generate(self, messages: list[dict], *, role: str = "editor") -> str:
        self.calls.append({"role": role, "messages": messages})
        return self.reply


class MockTools:
    def __init__(self, tools: list[dict] | None = None, results: dict | None = None) -> None:
        self._tools = tools or []
        self._results = results or {}
        self.calls = []

    def call(self, name: str, args: dict) -> dict:
        self.calls.append({"name": name, "args": args})
        return self._results.get(name, {"status": "ok"})

    def list_tools(self) -> list[dict]:
        return self._tools


def make_loop(tmp_path, **kwargs):
    log = EventLog(tmp_path / "events.jsonl")
    return AgentLoop(event_log=log, event_id_gen=EventIdGenerator(prefix="t"), **kwargs)


class TestStateMachine:
    def test_valid_transitions(self):
        sm = StateMachine()
        assert sm.state == AgentState.IDLE
        sm.transition(AgentState.PLANNING)
        sm.transition(AgentState.WAITING_TOOL)
        sm.transition(AgentState.EXECUTING)
        sm.transition(AgentState.FINISHED)
        assert sm.is_terminal()
        assert sm.termination_reason is None

    def test_invalid_transition_raises(self):
        sm = StateMachine()
        with pytest.raises(StateMachineError):
            sm.transition(AgentState.FINISHED)  # IDLE 不能直接到 FINISHED

    def test_max_iterations_termination(self):
        sm = StateMachine(max_iterations=3)
        sm.transition(AgentState.PLANNING)
        for _ in range(3):
            sm.transition(AgentState.WAITING_TOOL)
            sm.transition(AgentState.EXECUTING)
            sm.transition(AgentState.PLANNING)
        # 第 4 次工具调用应触发超步数终止
        result = sm.transition(AgentState.WAITING_TOOL)
        assert result is False
        assert sm.termination_reason == TerminationReason.MAX_ITERATIONS
        assert sm.state == AgentState.FINISHED

    def test_checkpoint_recording(self):
        sm = StateMachine()
        sm.transition(AgentState.PLANNING)
        sm.transition(AgentState.WAITING_TOOL)  # 打点 1（工具调用前）
        sm.transition(AgentState.EXECUTING)
        sm.transition(AgentState.PLANNING)
        sm.transition(AgentState.WAITING_TOOL)  # 打点 2
        assert len(sm.checkpoints) == 2

    def test_snapshot_restore(self):
        """会话中断恢复：快照可还原状态机。"""
        sm = StateMachine(max_iterations=100)
        sm.transition(AgentState.PLANNING)
        snap = sm.snapshot()
        sm2 = StateMachine()
        sm2.restore(snap)
        assert sm2.state == AgentState.PLANNING
        assert sm2.max_iterations == 100


class TestAgentLoop:
    def test_full_turn_with_llm_only(self, tmp_path):
        """无工具：LLM 规划后直接完成。"""
        llm = MockLLM()
        loop = make_loop(tmp_path, llm=llm, session_id="s1")
        loop.enqueue_op(UserTurnStart(content="修复 bug"))
        events = loop.run_until_idle()
        assert any(isinstance(e, TurnStarted) for e in events)
        assert loop.state_machine.state == AgentState.FINISHED
        assert loop.state_machine.termination_reason == TerminationReason.COMPLETED

    def test_tool_execution_green_risk(self, tmp_path):
        """低风险工具：自动执行不经审批。"""
        tools = MockTools(tools=[{"name": "Read", "args": {"path": "a.py"}}])
        loop = make_loop(tmp_path, llm=MockLLM(), tools=tools, session_id="s1")
        loop.enqueue_op(UserTurnStart(content="读文件"))
        loop.run_until_idle()
        assert tools.calls[0]["name"] == "Read"
        assert loop.state_machine.state == AgentState.FINISHED

    def test_dangerous_command_requires_approval(self, tmp_path):
        """高危命令：进入 WAITING_APPROVAL，留库不执行。"""
        tools = MockTools(tools=[{"name": "Bash", "args": {"command": "rm -rf /"}}])
        loop = make_loop(tmp_path, llm=MockLLM(), tools=tools, session_id="s1")
        loop.enqueue_op(UserTurnStart(content="清理"))
        events = loop.run_until_idle()
        approval = [e for e in events if isinstance(e, ExecApprovalRequest)]
        assert len(approval) == 1
        assert approval[0].risk_level == "red"
        assert tools.calls == []  # 未执行
        assert loop.state_machine.state == AgentState.WAITING_APPROVAL

    def test_approval_approve_executes(self, tmp_path):
        """批准后隐式执行。"""
        tools = MockTools(tools=[{"name": "Bash", "args": {"command": "rm -rf /"}}])
        loop = make_loop(tmp_path, llm=MockLLM(), tools=tools, session_id="s1")
        loop.enqueue_op(UserTurnStart(content="清理"))
        loop.run_until_idle()
        # 找到审批
        approval_id = loop._waiting_approval.approval_id
        loop.enqueue_op(ApprovalResponse(approval_id=approval_id, decision="approve"))
        loop.run_until_idle()
        assert tools.calls[0]["name"] == "Bash"
        assert loop.state_machine.state == AgentState.FINISHED

    def test_approval_reject_reflects(self, tmp_path):
        """拒绝后进入反思并完成（不执行）。"""
        tools = MockTools(tools=[{"name": "Bash", "args": {"command": "rm -rf /"}}])
        loop = make_loop(tmp_path, llm=MockLLM(), tools=tools, session_id="s1")
        loop.enqueue_op(UserTurnStart(content="清理"))
        loop.run_until_idle()
        approval_id = loop._waiting_approval.approval_id
        loop.enqueue_op(ApprovalResponse(approval_id=approval_id, decision="reject"))
        loop.run_until_idle()
        assert tools.calls == []
        assert loop.state_machine.state == AgentState.FINISHED

    def test_approval_idempotent(self, tmp_path):
        """同一审批重复提交只执行一次。"""
        tools = MockTools(tools=[{"name": "Bash", "args": {"command": "rm -rf /"}}])
        loop = make_loop(tmp_path, llm=MockLLM(), tools=tools, session_id="s1")
        loop.enqueue_op(UserTurnStart(content="清理"))
        loop.run_until_idle()
        approval_id = loop._waiting_approval.approval_id
        resp = ApprovalResponse(approval_id=approval_id, decision="approve")
        loop.enqueue_op(resp)
        loop.run_until_idle()
        n_calls_after_first = len(tools.calls)
        # 重放同一 Op（同 op_id）
        loop.enqueue_op(resp)
        loop.run_until_idle()
        assert len(tools.calls) == n_calls_after_first

    def test_op_queuing_when_busy(self, tmp_path):
        """提示排队：agent 忙时 Op 入队，跑完自动处理（pi-web prompt queuing）。"""
        tools = MockTools(tools=[{"name": "Read", "args": {"path": "a.py"}}])
        loop = make_loop(tmp_path, llm=MockLLM(), tools=tools, session_id="s1")
        # 第一个入队后手动 step 一次（进入 PLANNING，忙态）
        loop.enqueue_op(UserTurnStart(content="任务1"))
        loop.step()
        assert loop.state_machine.state == AgentState.PLANNING  # 忙
        # 忙时第二个入队 → 排队
        loop.enqueue_op(UserTurnStart(content="任务2"))
        assert len(loop._pending_ops) == 1
        # 跑完自动处理排队的第二个
        loop.run_until_idle()
        assert loop._turn_index >= 2
        assert loop.state_machine.state == AgentState.FINISHED

    def test_cancel(self, tmp_path):
        from src.protocol.ops import UserTurnCancel

        loop = make_loop(tmp_path, session_id="s1")
        loop.enqueue_op(UserTurnStart(content="任务"))
        loop.run_until_idle()
        loop.enqueue_op(UserTurnCancel(reason="不想做了"))
        loop.run_until_idle()
        assert loop.state_machine.state == AgentState.CANCELLED

    def test_all_events_persisted(self, tmp_path):
        """核心事件全部落 JSONL 事实源。"""
        log = EventLog(tmp_path / "events.jsonl")
        tools = MockTools(tools=[{"name": "Read", "args": {"path": "a.py"}}])
        loop = AgentLoop(event_log=log, llm=MockLLM(), tools=tools, session_id="s1")
        loop.enqueue_op(UserTurnStart(content="任务"))
        loop.run_until_idle()
        events = log.read_all()
        types = {e.type.value for e in events}
        assert "TurnStarted" in types
        assert "AgentMessageContentDelta" in types
        assert "ItemCompleted" in types


class TestG18ClassifierIntegration:
    """G18 自动安全分类器接入 AgentLoop（v1.23 落地）。"""

    def test_classifier_block_hard_deny(self, tmp_path):
        """hard-deny 命中 → 分类器拦截，不执行工具，不进入 EXECUTING。"""
        from src.security import AutoSafetyClassifier

        tools = MockTools(tools=[{"name": "Bash", "args": {"command": "rm -rf /"}}])
        loop = make_loop(tmp_path, llm=MockLLM(), tools=tools, session_id="s1")
        loop.set_classifier(AutoSafetyClassifier())
        loop.enqueue_op(UserTurnStart(content="清理"))
        loop.run_until_idle()
        # 工具未被调用（block 后不执行）
        assert tools.calls == []
        assert loop.state_machine.is_terminal()
        # ClassifierVerdict 事件落盘（审批即事件）
        events = loop.event_log.read_all()
        verdicts = [e for e in events if e.type.value == "ClassifierVerdict"]
        assert len(verdicts) >= 1
        assert verdicts[-1].decision == "block"

    def test_classifier_allow_executes(self, tmp_path):
        """allow → 工具正常执行。"""
        from src.security import AutoSafetyClassifier

        tools = MockTools(tools=[{"name": "Bash", "args": {"command": "ls -la"}}], results={"Bash": {"status": "ok", "exit_code": 0}})
        loop = make_loop(tmp_path, llm=MockLLM(), tools=tools, session_id="s1")
        loop.set_classifier(AutoSafetyClassifier())
        loop.enqueue_op(UserTurnStart(content="看目录"))
        loop.run_until_idle()
        assert len(tools.calls) == 1
        events = loop.event_log.read_all()
        verdicts = [e for e in events if e.type.value == "ClassifierVerdict"]
        assert verdicts and verdicts[-1].decision == "allow"

    def test_classifier_escalate_goes_approval(self, tmp_path):
        """escalate（存疑）→ 升级人工审批（ExecApprovalRequest + WAITING_APPROVAL）。"""
        from src.security import AutoSafetyClassifier

        # stage2 规则精判 escalate：危险工具但非 hard-deny（git push 非强推、长命令触发 stage2）
        tools = MockTools(tools=[{"name": "Bash", "args": {"command": "git status && git push origin feature-branch && git log --oneline -3"}}])
        loop = make_loop(tmp_path, llm=MockLLM(), tools=tools, session_id="s1")
        loop.set_classifier(AutoSafetyClassifier())
        loop.enqueue_op(UserTurnStart(content="推送分支"))
        loop.run_until_idle()
        events = loop.event_log.read_all()
        approvals = [e for e in events if e.type.value == "ExecApprovalRequest"]
        assert len(approvals) >= 1  # escalate → 人工审批
        verdicts = [e for e in events if e.type.value == "ClassifierVerdict"]
        assert verdicts and verdicts[-1].decision == "escalate"

    def test_classifier_tier1_bypass(self, tmp_path):
        """Tier1 工具不过分类器（零延迟）。"""
        from src.security import AutoSafetyClassifier

        tools = MockTools(tools=[{"name": "Read", "args": {"path": "a.py"}}])
        loop = make_loop(tmp_path, llm=MockLLM(), tools=tools, session_id="s1")
        loop.set_classifier(AutoSafetyClassifier())
        loop.enqueue_op(UserTurnStart(content="读文件"))
        loop.run_until_idle()
        assert len(tools.calls) == 1  # 直接执行
        events = loop.event_log.read_all()
        # Tier1 动作也产生判决事件（tier=1），但 decision=allow
        verdicts = [e for e in events if e.type.value == "ClassifierVerdict"]
        assert all(v.decision == "allow" for v in verdicts)

    def test_classifier_disabled_fallback_legacy(self, tmp_path):
        """分类器未注入 → 走既有风险评估（审批即事件）。"""
        tools = MockTools(tools=[{"name": "Bash", "args": {"command": "rm -rf /"}}])
        loop = make_loop(tmp_path, llm=MockLLM(), tools=tools, session_id="s1")
        loop.enqueue_op(UserTurnStart(content="清理"))
        loop.run_until_idle()
        events = loop.event_log.read_all()
        approvals = [e for e in events if e.type.value == "ExecApprovalRequest"]
        assert len(approvals) >= 1  # 旧审批流仍工作
