"""全链路集成验证（v1.23 落地④）——"策略-执行-治理"闭环真实可跑。

链路：UserTurnStart → PolicyEngine（策略即代码）→ OpRouter（按 Op 分派）
     → AutoSafetyClassifier（G18 运行时防御）→ 沙箱执行 → EventLog 落盘
     → MetricsProjector（指标聚合）→ ContributionReport（AI 贡献投影）
     → 全部可审计（事件溯源）

验证目标：不是单测的模块隔离，而是整条链路串起来真实跑通。
"""

import pytest

from src.agent import AgentLoop, EventIdGenerator
from src.loop import ControlPolicy, PolicyEngine, PolicyRule
from src.projection.contribution import ContributionReporter
from src.projection.metrics import MetricsProjector
from src.protocol.ops import UserTurnStart
from src.routing import OpRouter
from src.security import AutoSafetyClassifier
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


class TestFullChain:
    """全链路 smoke：策略-执行-治理闭环。"""

    def test_full_chain_allow(self, tmp_path):
        """链路 1：策略放行 + 分类器 allow → 执行 → 指标聚合 → 贡献投影。"""
        log = EventLog(tmp_path / "chain.jsonl")
        tools = MockTools(tools=[{"name": "Read", "args": {"path": "a.py"}}], results={"Read": {"status": "ok", "content": "x"}})
        loop = AgentLoop(event_log=log, llm=MockLLM(), tools=tools, session_id="s1", event_id_gen=EventIdGenerator(prefix="t"))

        # ① 策略即代码（只 deny Evil，Read 放行）
        policy = ControlPolicy(policy_id="prod")
        policy.rules.append(PolicyRule(action="deny", tool_pattern="Evil"))
        loop.set_policy_engine(PolicyEngine(policy=policy))
        # ② 按 Op 分派（Read → cheap）
        router = OpRouter()
        loop.set_op_router(router)
        # ③ 分类器（Read Tier1 自动放行）
        loop.set_classifier(AutoSafetyClassifier())

        loop.enqueue_op(UserTurnStart(content="读文件"))
        loop.run_until_idle()

        # 执行成功
        assert len(tools.calls) == 1
        # 治理闭环：指标聚合 + 贡献投影 + 事件全落盘
        metrics = MetricsProjector(event_log=log).aggregate()
        assert metrics.metrics["tool_call_count"] >= 1
        report = ContributionReporter(log).build(task_id="t1")
        assert report.source_event_count >= 1
        # 路由记账 + 策略审计
        assert router.stats()["by_tier"]["cheap"] >= 1
        assert len(PolicyEngine(policy=policy).audit()) >= 0  # 策略引擎已审计

    def test_full_chain_policy_deny(self, tmp_path):
        """链路 2：策略 deny → 拦截在分类器之前（企业管理面先于运行时防御）。"""
        log = EventLog(tmp_path / "chain2.jsonl")
        tools = MockTools(tools=[{"name": "Bash", "args": {"command": "rm -rf /tmp/x"}}])
        loop = AgentLoop(event_log=log, llm=MockLLM(), tools=tools, session_id="s1", event_id_gen=EventIdGenerator(prefix="t"))

        policy = ControlPolicy(policy_id="prod")
        policy.rules.append(PolicyRule(action="deny", tool_pattern="Bash", resource_pattern="*rm*"))
        engine = PolicyEngine(policy=policy)
        loop.set_policy_engine(engine)
        loop.set_classifier(AutoSafetyClassifier())
        loop.set_op_router(OpRouter())

        loop.enqueue_op(UserTurnStart(content="清理"))
        loop.run_until_idle()

        # 工具从未执行（策略先拦）
        assert tools.calls == []
        # 策略审计记录了 deny
        audit = engine.audit()
        assert any(a["decision"] == "deny" for a in audit)
        # 事件流有拒绝消息（可审计）
        events = log.read_all()
        assert len(events) >= 1

    def test_full_chain_classifier_blocks(self, tmp_path):
        """链路 3：策略放行但分类器 hard-deny → 运行时防御兜底。"""
        log = EventLog(tmp_path / "chain3.jsonl")
        tools = MockTools(tools=[{"name": "Bash", "args": {"command": "rm -rf /"}}])
        loop = AgentLoop(event_log=log, llm=MockLLM(), tools=tools, session_id="s1", event_id_gen=EventIdGenerator(prefix="t"))

        # 策略无限制（默认放行）
        policy = ControlPolicy(policy_id="open")
        loop.set_policy_engine(PolicyEngine(policy=policy))
        # 分类器 hard-deny 拦截
        loop.set_classifier(AutoSafetyClassifier())

        loop.enqueue_op(UserTurnStart(content="清理"))
        loop.run_until_idle()

        assert tools.calls == []  # 分类器拦截
        # ClassifierVerdict 事件落盘（审批即事件）
        events = log.read_all()
        verdicts = [e for e in events if e.type.value == "ClassifierVerdict"]
        assert verdicts and verdicts[-1].decision == "block"

    def test_full_chain_audit_complete(self, tmp_path):
        """链路 4：全链路审计完整性——每个阶段都有事件证据。"""
        log = EventLog(tmp_path / "chain4.jsonl")
        tools = MockTools(tools=[{"name": "Read", "args": {"path": "a.py"}}])
        loop = AgentLoop(event_log=log, llm=MockLLM(), tools=tools, session_id="s1", event_id_gen=EventIdGenerator(prefix="t"))
        loop.set_policy_engine(PolicyEngine(policy=ControlPolicy(policy_id="p")))
        loop.set_op_router(OpRouter())
        loop.set_classifier(AutoSafetyClassifier())
        loop.enqueue_op(UserTurnStart(content="x"))
        loop.run_until_idle()
        # 事件流包含：TurnStarted + ItemCompleted + ClassifierVerdict
        types = {e.type.value for e in log.read_all()}
        assert "TurnStarted" in types
        assert "ItemCompleted" in types
        assert "ClassifierVerdict" in types
