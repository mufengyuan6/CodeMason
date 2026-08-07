"""v1.28 G20 测试：FixPacket 接线（staging Hook 失败→机读契约）+ RootCauseAnalyzer 五段闭环。

对应 design.md G20 ①③④ + 差异表 FixPacket 接线行（从半实现变为溯源消费端）。
"""

import time

import pytest

from src.projection.root_cause import RootCauseQuerier
from src.projection.root_cause_analyzer import AttributionHypothesis, RootCauseAnalyzer
from src.protocol import ErrorEvent, ItemCompleted, RootCauseReport, TurnStarted
from src.staging.sandbox import StagedChange, StagingSandbox
from src.storage import EventLog


def _now() -> float:
    return time.time()


def _seed_log(tmp_path) -> EventLog:
    log = EventLog(tmp_path / "events.jsonl")
    events = [
        TurnStarted(id=1, session_id="s1", mode="act", turn_index=1, op_id="o1", ts=_now()),
        ItemCompleted(
            id=2, session_id="s1", item_type="tool_result", item_id="Edit-1",
            content={"status": "ok"}, ts=_now(),
        ),
        ErrorEvent(
            id=3, session_id="s1", message="SyntaxError: unexpected indent", error_type="syntax",
            failure_stage="edit", related_tool="Edit", ts=_now(),
        ),
    ]
    for ev in events:
        log.append(ev)
    return log


class TestFixPacketWiring:
    """FixPacket 接线：staging Hook 失败 → 机读契约（file+line+hint+修复指令）。"""

    def _blocking_hook(self, change):
        return {
            "hook": "yagni",
            "blocked": True,
            "reason": {
                "findings": [
                    {"rule": "L7", "file": change.path, "line": 10, "message": "圈复杂度超阈值", "severity": "block"}
                ]
            },
        }

    def test_hook_failure_emits_fix_packet(self, tmp_path):
        sandbox = StagingSandbox(hooks=[self._blocking_hook])
        change = sandbox.stage("src/a.py", "old", "new")
        passed = sandbox.run_hooks(change)
        assert passed is False
        assert change.status == "blocked"
        # v1.28：失败产出 FixPacket 机读契约
        packet = sandbox.last_fix_packet
        assert packet is not None
        assert packet["stage"] == "staging_apply"
        assert packet["status"] == "failed"
        assert packet["violations"][0]["code"] == "L7"
        assert packet["violations"][0]["file"] == "src/a.py"
        assert packet["violations"][0]["line"] == 10

    def test_hook_failure_string_reason(self, tmp_path):
        sandbox = StagingSandbox(hooks=[lambda c: {"hook": "check", "blocked": True, "reason": "不允许修改"}])
        change = sandbox.stage("src/b.py", "old", "new")
        sandbox.run_hooks(change)
        packet = sandbox.last_fix_packet
        assert packet["violations"][0]["code"] == "HOOK_FAIL"
        assert "不允许修改" in packet["violations"][0]["message"]

    def test_hook_pass_no_fix_packet(self, tmp_path):
        sandbox = StagingSandbox(hooks=[lambda c: {"hook": "ok", "blocked": False}])
        change = sandbox.stage("src/c.py", "old", "new")
        passed = sandbox.run_hooks(change)
        assert passed is True
        assert sandbox.last_fix_packet is None  # 无失败不产出

    def test_exception_hook_emits_packet(self, tmp_path):
        def boom(change):
            raise RuntimeError("hook crash")

        sandbox = StagingSandbox(hooks=[boom])
        change = sandbox.stage("src/d.py", "old", "new")
        sandbox.run_hooks(change)
        assert sandbox.last_fix_packet["violations"][0]["message"] == "hook crash"


class TestRootCauseAnalyzer:
    """G20 五段闭环：确定性证据链 → 归因假设 → 溯源报告 → 诊断回喂 → 沉淀。"""

    def _attribution_fn(self, **kw):
        return [AttributionHypothesis(hypothesis="缩进错误导致语法失败", confidence=0.85)]

    def _analyzer(self, tmp_path, **kw) -> RootCauseAnalyzer:
        log = _seed_log(tmp_path)
        return RootCauseAnalyzer(log, session_id="s1", attribution_fn=self._attribution_fn, **kw)

    def test_analyze_complete_loop(self, tmp_path):
        analyzer = self._analyzer(tmp_path)
        report, feed = analyzer.analyze(trigger="verify_failed", trigger_event_id=3, session_id="s1")
        # 报告结构
        assert isinstance(report, RootCauseReport)
        assert report.trigger == "verify_failed"
        assert report.status == "completed"  # LLM 归因可用
        assert report.trigger_event_id == 3
        # 证据链：失败链含锚点错误
        assert any(f["id"] == 3 for f in report.evidence["failure_chain"])
        # 归因假设：agent_inferred 永不升级
        assert all(a["agent_inferred"] is True for a in report.attributions)
        assert report.attributions[0]["hypothesis"] == "缩进错误导致语法失败"
        # 三阶段定位（failure_stage=edit）
        assert any(s["stage"] == "edit" for s in report.stages)
        # 修复指令非空
        assert report.fix_instructions
        # 诊断回喂载荷
        assert feed["report_id"] == report.report_id
        assert "诊断回喂" in feed["prompt_fragment"]
        # ⑤ 沉淀：溯源即事件——报告已落盘
        loaded = log_get_by_id(analyzer.event_log, report.id)
        assert loaded is not None and loaded.report_id == report.report_id

    def test_analyze_degraded_without_llm(self, tmp_path):
        """无 LLM 归因 → 纯确定性证据链（status=degraded，仍可审计）。"""
        analyzer = RootCauseAnalyzer(_seed_log(tmp_path), session_id="s1")  # 无 attribution_fn
        report, feed = analyzer.analyze(trigger="error", trigger_event_id=3, session_id="s1")
        assert report.status == "degraded"
        assert report.attributions == []
        assert feed["status"] == "degraded"

    def test_analyze_auto_anchor_latest_failure(self, tmp_path):
        """trigger_event_id=0 → 自动取最近失败事件。"""
        analyzer = self._analyzer(tmp_path)
        report, _ = analyzer.analyze(trigger="user_query", session_id="s1")
        assert report.trigger_event_id == 3  # 唯一失败事件

    def test_analyze_with_fix_packet_injection(self, tmp_path):
        """FixPacket 注入 → 修复指令机读可消费（闭环）。"""
        analyzer = self._analyzer(tmp_path)
        fp = {
            "packet_id": "fp-1",
            "stage": "staging_apply",
            "violations": [
                {"code": "L7", "file": "src/a.py", "line": 10, "message": "圈复杂度超阈值", "hint": "拆函数", "severity": "block"}
            ],
            "status": "failed",
        }
        report, feed = analyzer.analyze(
            trigger="verify_failed", trigger_event_id=3, session_id="s1", fix_packets=[fp]
        )
        assert any("L7" in i for i in report.fix_instructions)
        assert any("拆函数" in i for i in report.fix_instructions)

    def test_analyze_no_failure_events_safe(self, tmp_path):
        """空事件流安全（无失败 → 报告仍生成但证据为空）。"""
        log = EventLog(tmp_path / "empty.jsonl")
        analyzer = RootCauseAnalyzer(log, session_id="s1")
        report, _ = analyzer.analyze(trigger="user_query", session_id="s1")
        assert report.status == "degraded"
        assert report.evidence["failure_chain"] == []

    def test_attribution_exception_degrades(self, tmp_path):
        """LLM 归因抛异常 → 降级纯确定性（不崩）。"""
        def bad_fn(**kw):
            raise RuntimeError("llm down")

        analyzer = RootCauseAnalyzer(_seed_log(tmp_path), session_id="s1", attribution_fn=bad_fn)
        report, feed = analyzer.analyze(trigger="error", trigger_event_id=3, session_id="s1")
        assert report.status == "degraded"
        assert report.attributions == []


def log_get_by_id(event_log, event_id):
    return event_log.get(event_id)
