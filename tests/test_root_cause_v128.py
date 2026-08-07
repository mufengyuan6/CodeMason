"""v1.28 G20 溯源查询层测试：失败事件过滤 + 失败链回溯 + 证据集组装（纯确定性零 LLM）。

对应 design.md G20 ①确定性证据链（事件流失败链回溯）。
"""

import time

import pytest

from src.projection.root_cause import (
    FAILURE_EVENT_TYPES,
    FailureChain,
    FailureIndexUnit,
    RootCauseQuerier,
)
from src.protocol import (
    ErrorEvent,
    EventType,
    ExecApprovalRequest,
    ItemCompleted,
    Rollback,
    TraceRecord,
    TurnStarted,
)
from src.storage import EventLog


def _mk_log(tmp_path) -> EventLog:
    return EventLog(tmp_path / "events.jsonl")


def _now() -> float:
    return time.time()


class TestFailureIndexUnit:
    """失败事件索引投影单元（v1.26 投影纪律：幂等 + 同引用无工作）。"""

    def test_index_collects_failure_types(self):
        unit = FailureIndexUnit()
        state = unit.init()
        log = EventLog.__new__(EventLog)  # 无需真实存储，仅验证 apply 纯函数
        evs = [
            ErrorEvent(id=1, session_id="s1", message="e1", ts=_now()),
            TurnStarted(id=2, session_id="s1", mode="act", turn_index=1, op_id="o1", ts=_now()),
            ErrorEvent(id=3, session_id="s1", message="e2", ts=_now()),
        ]
        for ev in evs:
            state = unit.apply(state, ev)
        assert state["by_session"]["s1"][0]["message"] == "e1"
        assert state["by_session"]["s1"][1]["message"] == "e2"
        # TurnStarted 不是失败类型 → 不入索引
        assert len(state["by_id"]) == 2

    def test_index_idempotent_same_reference(self):
        unit = FailureIndexUnit()
        state = unit.init()
        ev = ErrorEvent(id=5, session_id="s1", message="x", ts=_now())
        state1 = unit.apply(state, ev)
        state2 = unit.apply(state1, ev)  # 同一事件重复 apply
        assert state1 is state2  # 幂等：返回同一引用

    def test_index_unrelated_event_same_reference(self):
        unit = FailureIndexUnit()
        state = unit.init()
        ev = TurnStarted(id=1, session_id="s1", mode="act", turn_index=1, op_id="o", ts=_now())
        assert unit.apply(state, ev) is state  # 无关事件 → 同引用（Object.is 闸门）

    def test_view_summary(self):
        unit = FailureIndexUnit()
        state = unit.init()
        for i in range(3):
            state = unit.apply(
                state, ErrorEvent(id=i + 1, session_id="s1", message=f"e{i}", ts=_now())
            )
        view = unit.view(state)
        assert view["count"] == 3
        assert len(view["by_session"]["s1"]) == 3


class TestRootCauseQuerier:
    """溯源查询层：失败事件过滤 + 失败链回溯 + 证据集组装。"""

    def _seed_log(self, tmp_path) -> EventLog:
        """构造一个带失败链的事件流：工具调用 → 错误 → 回滚 → 后续成功。"""
        log = _mk_log(tmp_path)
        events = [
            TurnStarted(id=1, session_id="s1", mode="act", turn_index=1, op_id="o1", ts=_now()),
            ExecApprovalRequest(
                id=2, session_id="s1", approval_id="a1", tool_name="Edit",
                description="改文件", command="edit a.py", risk_level="yellow", ts=_now(),
            ),
            ItemCompleted(
                id=3, session_id="s1", item_type="tool_result", item_id="Edit-1",
                content={"status": "ok"}, ts=_now(),
            ),
            ErrorEvent(
                id=4, session_id="s1", message="SyntaxError in a.py", error_type="syntax",
                failure_stage="edit", related_tool="Edit", ts=_now(),
            ),
            Rollback(id=5, session_id="s1", checkpoint_id="cp-1", reason="verify_failed", ts=_now()),
            TraceRecord(
                id=6, session_id="s1", trace_id="t1", executor="local", command="pytest a.py",
                exit_code=1, ts=_now(),
            ),
            ItemCompleted(
                id=7, session_id="s1", item_type="turn_summary", item_id="turn-1",
                content={"status": "failed"}, ts=_now(),
            ),
        ]
        for ev in events:
            log.append(ev)
        return log

    def test_failure_events_filter_by_session(self, tmp_path):
        log = self._seed_log(tmp_path)
        querier = RootCauseQuerier(log)
        failures = querier.failure_events(session_id="s1")
        types = {f["type"] for f in failures}
        assert EventType.ERROR.value in types
        assert EventType.ROLLBACK.value in types
        assert all(f["session_id"] == "s1" for f in failures)

    def test_failure_events_empty_session(self, tmp_path):
        log = self._seed_log(tmp_path)
        querier = RootCauseQuerier(log)
        assert querier.failure_events(session_id="no-such") == []

    def test_trace_failure_chain_assembles_evidence(self, tmp_path):
        log = self._seed_log(tmp_path)
        querier = RootCauseQuerier(log)
        # 锚点 = 最近失败（Rollback，失败链末端）——从它回溯到 Error
        chain = querier.trace_failure_chain(anchor_event_id=5, session_id="s1")
        assert isinstance(chain, FailureChain)
        # 失败链包含锚点错误 + 回滚
        assert any(f["id"] == 4 for f in chain.failures)
        assert any(f["type"] == EventType.ROLLBACK.value for f in chain.failures)
        # 相关上下文包含审批 + 工具结果
        kinds = {r["kind"] for r in chain.related_events}
        assert "approval" in kinds
        assert "tool_result" in kinds
        # 轨迹
        assert any(t["command"] == "pytest a.py" for t in chain.trace_records)

    def test_trace_chain_anchor_missing_returns_empty(self, tmp_path):
        """锚点不存在 → 空失败链（不是崩溃；G20 空事件流安全）。"""
        log = self._seed_log(tmp_path)
        querier = RootCauseQuerier(log)
        chain = querier.trace_failure_chain(anchor_event_id=9999)
        assert chain.failures == []
        assert chain.anchor_event_id == 9999

    def test_trace_chain_sessions_isolated(self, tmp_path):
        log = self._seed_log(tmp_path)
        querier = RootCauseQuerier(log)
        chain = querier.trace_failure_chain(anchor_event_id=4, session_id="other")
        assert chain.failures == []

    def test_injection_slots(self, tmp_path):
        """YAGNI 外环 / FixPacket 契约 / 图谱影响面注入槽。"""
        log = self._seed_log(tmp_path)
        querier = RootCauseQuerier(log)
        chain = querier.trace_failure_chain(
            anchor_event_id=5,
            session_id="s1",
            yagni_findings=[{"rule": "L5", "file": "a.py"}],
            fix_packets=[{"packet_id": "fp-1", "file": "a.py", "line": 10}],
            impact_scope=[{"name": "Foo.bar"}],
        )
        assert chain.yagni_findings == [{"rule": "L5", "file": "a.py"}]
        assert chain.fix_packets[0]["packet_id"] == "fp-1"
        assert any(r["kind"] == "impact_scope" for r in chain.related_events)

    def test_build_report_event_roundtrip(self, tmp_path):
        """证据集 → RootCauseReport 事件（G20 ⑤ 溯源即事件）。"""
        log = self._seed_log(tmp_path)
        querier = RootCauseQuerier(log)
        chain = querier.trace_failure_chain(anchor_event_id=5, session_id="s1")
        report = querier.build_report_event(
            report_id="rc-1",
            trigger="verify_failed",
            trigger_event_id=4,
            chain=chain,
            attributions=[{"hypothesis": "h1", "agent_inferred": True}],
            stages=[{"stage": "edit", "file": "a.py", "line": 10, "confidence": 0.9}],
            fix_instructions=["修正语法错误"],
            session_id="s1",
        )
        assert report.report_id == "rc-1"
        assert report.trigger == "verify_failed"
        assert report.evidence["failure_chain"][0]["id"] == 4
        assert report.attributions[0]["agent_inferred"] is True
        assert report.stages[0]["stage"] == "edit"
        assert report.fix_instructions == ["修正语法错误"]
        # 落盘后可读回（append 返回实际落盘 id——预分配 id 可能被 append 兜底递增）
        real_id = log.append(report)
        loaded = log.get(real_id)
        assert loaded.report_id == "rc-1"
