"""v1.23 落地④ AgentLoop 集成测试：scheduler/budget/op_router/policy 接入。

验收：
- PolicyEngine 注入：deny → 工具不执行（策略即代码生效）
- OpRouter 注入：工具调用走分派（成本归因）
- LoopBudget 注入：超限熔断（不执行新工具）
- LoopScheduler 注入：调度触发 → UserTurnStart 入队
- 策略先于分类器（企业管理面先于运行时防御）
"""

import pytest

from src.agent import AgentLoop, AgentState, EventIdGenerator
from src.loop import ControlPolicy, LoopBudget, LoopScheduler, PolicyEngine
from src.protocol.ops import UserTurnStart
from src.routing import OpRouter
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


class TestPolicyIntegration:
    """策略即代码接入 AgentLoop。"""

    def test_policy_deny_blocks_tool(self, tmp_path):
        """策略 deny → 工具不执行。"""
        policy = ControlPolicy(policy_id="prod")
        from src.loop import PolicyRule

        policy.rules.append(PolicyRule(action="deny", tool_pattern="Bash", resource_pattern="*rm*"))
        engine = PolicyEngine(policy=policy)
        tools = MockTools(tools=[{"name": "Bash", "args": {"command": "rm -rf /tmp/x"}}])
        loop = make_loop(tmp_path, llm=MockLLM(), tools=tools, session_id="s1")
        loop.set_policy_engine(engine)
        loop.enqueue_op(UserTurnStart(content="清理"))
        loop.run_until_idle()
        assert tools.calls == []  # 策略拦截，工具未执行
        assert loop.state_machine.is_terminal()

    def test_policy_allow_tool_executes(self, tmp_path):
        """策略放行 → 工具正常执行。"""
        policy = ControlPolicy(policy_id="p")
        from src.loop import PolicyRule

        policy.rules.append(PolicyRule(action="deny", tool_pattern="Evil"))
        engine = PolicyEngine(policy=policy)
        tools = MockTools(tools=[{"name": "Read", "args": {"path": "a.py"}}], results={"Read": {"status": "ok"}})
        loop = make_loop(tmp_path, llm=MockLLM(), tools=tools, session_id="s1")
        loop.set_policy_engine(engine)
        loop.enqueue_op(UserTurnStart(content="读文件"))
        loop.run_until_idle()
        assert len(tools.calls) == 1  # 未命中 deny → 放行

    def test_policy_require_approval(self, tmp_path):
        """require_approval → 升级人工审批（ExecApprovalRequest）。"""
        policy = ControlPolicy(policy_id="p")
        from src.loop import PolicyRule

        policy.rules.append(PolicyRule(action="require_approval", tool_pattern="Write"))
        engine = PolicyEngine(policy=policy)
        tools = MockTools(tools=[{"name": "Write", "args": {"path": "src/x.py"}}])
        loop = make_loop(tmp_path, llm=MockLLM(), tools=tools, session_id="s1")
        loop.set_policy_engine(engine)
        loop.enqueue_op(UserTurnStart(content="写文件"))
        loop.run_until_idle()
        events = loop.event_log.read_all()
        approvals = [e for e in events if e.type.value == "ExecApprovalRequest"]
        assert len(approvals) >= 1  # 策略要求审批


class TestOpRouterIntegration:
    """按 Op 分派记账接入。"""

    def test_router_records_calls(self, tmp_path):
        router = OpRouter()
        tools = MockTools(tools=[{"name": "Read", "args": {"path": "a.py"}}])
        loop = make_loop(tmp_path, llm=MockLLM(), tools=tools, session_id="s1")
        loop.set_op_router(router)
        loop.enqueue_op(UserTurnStart(content="读文件"))
        loop.run_until_idle()
        stats = router.stats()
        assert stats["total_routes"] >= 1
        assert stats["by_tier"]["cheap"] >= 1  # Read → cheap


class TestBudgetIntegration:
    """token 硬预算熔断接入。"""

    def test_budget_trip_blocks_new_tools(self, tmp_path):
        budget = LoopBudget(hard_limit=10)
        tools = MockTools(tools=[{"name": "Bash", "args": {"command": "ls"}}])
        loop = make_loop(tmp_path, llm=MockLLM(), tools=tools, session_id="s1")
        loop.set_budget(budget)
        budget.record(20)  # 预先超限
        loop.enqueue_op(UserTurnStart(content="x"))
        loop.run_until_idle(max_steps=10)
        # 超限熔断：不执行工具
        assert tools.calls == []


class TestSchedulerIntegration:
    """调度触发接入（协议生产者）。"""

    def test_scheduler_enqueues_via_loop(self, tmp_path):
        """scheduler 的 enqueue 回调 → loop.enqueue_op(UserTurnStart)。"""
        from src.protocol.ops import UserTurnStart

        log = EventLog(tmp_path / "s.jsonl")
        loop = AgentLoop(event_log=log, session_id="s1", event_id_gen=EventIdGenerator(prefix="t"))
        # scheduler 的 enqueue 适配 loop
        sched = LoopScheduler(enqueue=lambda task, trigger: loop.enqueue_op(UserTurnStart(content=task, mode="act")) or True)
        sched.add_event_trigger("pr-review", "pull_request_opened", "review PR")
        sched.on_event("pull_request_opened")
        assert any(isinstance(op, UserTurnStart) for op in loop._pending_ops)
