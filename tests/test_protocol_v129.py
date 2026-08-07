"""v1.29 协议层测试：rationale 字段（Op 可解释性）+ Error 增强（G20 溯源）+ RootCauseReport 事件。

对应 design.md v1.29（G3 协议层补 Op rationale 字段）+ v1.28（G20 ⑤ 溯源即事件）。
"""

import json
import time

import pytest

from src.protocol import (
    ErrorEvent,
    EventType,
    ExecApprovalRequest,
    ItemCompleted,
    RootCauseReport,
    event_to_json,
    parse_event,
)


def _now() -> float:
    return time.time()


class TestRationaleField:
    """v1.29：Op rationale 字段——工具调用事件携带模型一句话理由（≤20 词），
    rationale_source=model_self_report 非验证事实（对标 fact-checker 三态声明态）。"""

    def test_exec_approval_carries_rationale(self):
        ev = ExecApprovalRequest(
            id=1, session_id="s1", approval_id="a1", tool_name="Bash",
            description="跑测试", command="pytest", risk_level="yellow",
            rationale="验证改动是否破坏现有功能",
            ts=_now(),
        )
        assert ev.rationale == "验证改动是否破坏现有功能"
        assert ev.rationale_source == "model_self_report"

    def test_item_completed_carries_rationale(self):
        ev = ItemCompleted(
            id=2, session_id="s1", item_type="tool_result", item_id="Bash-1",
            content={"status": "ok"}, rationale="先搜引用再改定义",
            ts=_now(),
        )
        assert ev.rationale == "先搜引用再改定义"

    def test_rationale_optional_backward_compat(self):
        """schema 向后兼容：rationale 可缺省（driver 场景可关），旧事件仍可解析。"""
        ev = ExecApprovalRequest(
            id=3, session_id="s1", approval_id="a2", tool_name="Read",
            description="读文件", risk_level="green", ts=_now(),
        )
        assert ev.rationale is None
        # 序列化 roundtrip
        raw = event_to_json(ev)
        parsed = parse_event(raw)
        assert parsed.rationale is None
        assert parsed.rationale_source == "model_self_report"

    def test_rationale_roundtrip_through_jsonl(self):
        ev = ItemCompleted(
            id=4, session_id="s1", item_type="tool_result", item_id="Grep-1",
            content={"matches": 2}, rationale="确认调用方位置",
            ts=_now(),
        )
        raw = event_to_json(ev)
        parsed = parse_event(raw)
        assert parsed.rationale == "确认调用方位置"
        assert parsed.rationale_source == "model_self_report"

    def test_rationale_source_explicit_mark(self):
        """rationale_source 是显式标注非验证事实——事件 JSON 里必须有该字段。"""
        ev = ItemCompleted(
            id=5, session_id="s1", item_type="tool_result", item_id="Bash-2",
            rationale="重跑失败用例", ts=_now(),
        )
        data = json.loads(event_to_json(ev))
        assert data["rationale_source"] == "model_self_report"


class TestErrorEnhanced:
    """v1.28：Error 事件补 failure_stage（TRAJEVAL 三阶段口径）+ related_tool（溯源过滤）。"""

    def test_error_carries_stage_and_tool(self):
        ev = ErrorEvent(
            id=6, session_id="s1", message="找不到函数定义",
            error_type="logical", failure_stage="read", related_tool="Grep",
            ts=_now(),
        )
        assert ev.failure_stage == "read"
        assert ev.related_tool == "Grep"

    def test_error_stage_optional_backward_compat(self):
        ev = ErrorEvent(id=7, session_id="s1", message="网络超时", ts=_now())
        assert ev.failure_stage is None
        assert ev.related_tool is None
        parsed = parse_event(event_to_json(ev))
        assert parsed.failure_stage is None

    def test_error_roundtrip(self):
        ev = ErrorEvent(
            id=8, session_id="s1", message="改错文件", error_type="logical",
            failure_stage="edit", related_tool="Edit", ts=_now(),
        )
        parsed = parse_event(event_to_json(ev))
        assert parsed.failure_stage == "edit"
        assert parsed.related_tool == "Edit"


class TestRootCauseReportEvent:
    """v1.28：RootCauseReport 事件——溯源即事件，可审计可回放（G20 ⑤沉淀）。"""

    def _report(self, **kw) -> RootCauseReport:
        base = dict(
            id=10, session_id="s1", report_id="rc-1",
            trigger="verify_failed", trigger_event_id=42,
            status="completed",
            evidence={
                "call_chain": [{"entity": "Foo.bar", "callers": ["Baz.qux"]}],
                "failure_chain": [{"event_id": 42, "type": "Error", "stage": "verify"}],
                "yagni_findings": [{"rule": "L5", "file": "src/a.py"}],
                "fix_packets": [{"packet_id": "fp-1", "file": "src/a.py", "line": 12}],
            },
            attributions=[
                {"hypothesis": "函数签名变更未同步调用方", "confidence": 0.8, "agent_inferred": True}
            ],
            stages=[
                {"stage": "read", "file": "src/a.py", "line": 12, "issue": "读错文件", "confidence": 0.9}
            ],
            fix_instructions=["把调用方参数改为新签名"],
            ts=_now(),
        )
        base.update(kw)
        return RootCauseReport(**base)

    def test_report_roundtrip(self):
        ev = self._report()
        assert ev.type == EventType.ROOT_CAUSE_REPORT
        parsed = parse_event(event_to_json(ev))
        assert parsed.report_id == "rc-1"
        assert parsed.trigger == "verify_failed"
        assert parsed.evidence["failure_chain"][0]["event_id"] == 42
        assert parsed.stages[0]["stage"] == "read"
        assert parsed.fix_instructions == ["把调用方参数改为新签名"]

    def test_attributions_agent_inferred_flag(self):
        """归因假设必须带 agent_inferred 标记（永不自动升级，ADR-05）。"""
        ev = self._report()
        assert all(a.get("agent_inferred") is True for a in ev.attributions)

    def test_degraded_status_allowed(self):
        """LLM 降级（仅确定性证据链）→ status=degraded，事件仍合法。"""
        ev = self._report(status="degraded", attributions=[])
        parsed = parse_event(event_to_json(ev))
        assert parsed.status == "degraded"
        assert parsed.attributions == []

    def test_feed_forward_payload(self):
        ev = self._report(feed_forward={"injected_turn": 3, "injected_step": 1, "prompt_fragment": "..."})
        parsed = parse_event(event_to_json(ev))
        assert parsed.feed_forward["injected_turn"] == 3

    def test_report_is_in_union(self):
        """判别联合反序列化：RootCauseReport 从 JSON 行正确识别 type。"""
        ev = self._report()
        parsed = parse_event(json.loads(event_to_json(ev)))
        assert isinstance(parsed, RootCauseReport)
