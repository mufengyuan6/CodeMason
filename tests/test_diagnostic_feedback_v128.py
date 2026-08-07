"""v1.28 G20 测试：诊断回喂接线（AgentLoop 失败→溯源→下一轮注入）+ YAGNI 高频问题归因报告。

对应 design.md G20 ④诊断回喂（CodeTracer 反思回放）+ YAGNI 高频问题归因报告（代码评审场景）。
"""

import time

import pytest

from src.agent.loop import AgentLoop
from src.constraints.yagni import YagniEngine
from src.constraints.yagni_attribution import YagniAttributionReporter
from src.projection.root_cause import RootCauseQuerier
from src.projection.root_cause_analyzer import RootCauseAnalyzer
from src.protocol import UserTurnStart
from src.storage import EventLog


class _RecordingLLM:
    """记录每次规划输入（验证诊断注入）。"""

    def __init__(self):
        self.plans = []

    def generate(self, messages, *, role="editor"):
        self.plans.append(messages[0]["content"])
        return "plan done"


class _RecordingTools:
    def __init__(self, results):
        self._results = list(results)

    def call(self, name, args):
        return {"status": "ok"}

    def list_tools(self):
        return []


class TestDiagnosticFeedback:
    """AgentLoop 诊断回喂：Error → 溯源 → 注入下一轮修复。"""

    def _loop_with_diagnostic(self, tmp_path):
        log = EventLog(tmp_path / "events.jsonl")
        llm = _RecordingLLM()
        loop = AgentLoop(event_log=log, llm=llm, session_id="s1")
        analyzer = RootCauseAnalyzer(log, session_id="s1")
        loop.set_root_cause_analyzer(analyzer)
        return loop, llm, log

    def _trigger_error(self, loop, content="Boom", **meta):
        """在合法状态（EXECUTING）触发内部错误——真实链路工具失败路径。"""
        from src.agent.events import InternalEvent, InternalEventType
        from src.agent.state_machine import AgentState

        loop.state_machine.force_set(AgentState.EXECUTING)
        meta.setdefault("error_type", "syntax")
        loop._handle_internal(
            InternalEvent(type=InternalEventType.ERROR, content=content, turn_index=1, meta=meta)
        )

    def test_error_triggers_root_cause_analyze(self, tmp_path):
        """Error 内部事件 → 溯源报告落盘（RootCauseReport 事件）。"""
        loop, llm, log = self._loop_with_diagnostic(tmp_path)
        self._trigger_error(loop, failure_stage="edit", related_tool="Edit")
        # 溯源报告应已落盘
        from src.protocol import EventType

        reports = [e for e in log.read_all() if e.type == EventType.ROOT_CAUSE_REPORT]
        assert len(reports) == 1
        assert reports[0].trigger == "error"
        assert reports[0].status == "degraded"  # 无 LLM 归因 → 纯确定性

    def test_diagnostic_injected_next_turn(self, tmp_path):
        """失败后下一轮规划注入诊断片段（CodeTracer 反思回放）。"""
        loop, llm, log = self._loop_with_diagnostic(tmp_path)
        self._trigger_error(loop)
        # 终态后新一轮 → 规划应带诊断片段
        loop.enqueue_op(UserTurnStart(content="fix it"))
        loop.run_until_idle(max_steps=5)
        assert len(llm.plans) >= 1
        assert "诊断回喂" in llm.plans[-1] or "溯源报告" in llm.plans[-1]

    def test_no_diagnostic_without_analyzer(self, tmp_path):
        """未接分析器 → 不注入诊断（不破坏现有行为）。"""
        log = EventLog(tmp_path / "events.jsonl")
        llm = _RecordingLLM()
        loop = AgentLoop(event_log=log, llm=llm, session_id="s1")  # 无 analyzer
        self._trigger_error(loop)
        loop.enqueue_op(UserTurnStart(content="fix"))
        loop.run_until_idle(max_steps=5)
        assert "诊断回喂" not in llm.plans[-1]

    def test_error_event_has_stage_and_tool(self, tmp_path):
        """Error 事件带 failure_stage/related_tool（G20 溯源依据）。"""
        log = EventLog(tmp_path / "events.jsonl")
        loop = AgentLoop(event_log=log, session_id="s1")
        self._trigger_error(loop, failure_stage="read", related_tool="Grep")
        errors = [e for e in log.read_all() if e.type.value == "Error"]
        assert errors[0].failure_stage == "read"
        assert errors[0].related_tool == "Grep"


class TestYagniAttributionReport:
    """YAGNI 高频问题归因报告：失败/变更相关聚合（事件驱动约束，非全库体检）。"""

    def _yagni_report(self, findings):
        from src.constraints.yagni import YagniReport, YagniFinding

        return YagniReport(findings=[YagniFinding(**f) for f in findings])

    def test_aggregates_by_rule(self):
        reporter = YagniAttributionReporter()
        r1 = self._yagni_report([
            {"rule": "L7", "level": 7, "file": "a.py", "line": 1, "message": "圈复杂度超", "severity": "block"},
            {"rule": "L5", "level": 5, "file": "a.py", "message": "未使用依赖", "severity": "info"},
        ])
        r2 = self._yagni_report([
            {"rule": "L7", "level": 7, "file": "b.py", "message": "函数名过长", "severity": "warn"},
        ])
        reporter.ingest(r1, failure_event_id=3)
        reporter.ingest(r2, failure_event_id=3)
        top = reporter.top_issues()
        assert top[0]["rule"] == "L7"  # 频次最高
        assert top[0]["count"] == 2
        assert top[0]["block_count"] == 1
        assert 3 in top[0]["failure_event_ids"]
        assert len(top[0]["sample_files"]) == 2

    def test_block_weighted_first(self):
        """block 项加权：count 相同但 block 多者排前。"""
        reporter = YagniAttributionReporter()
        reporter.ingest_report_dict(
            {"findings": [{"rule": "L2", "file": "a.py", "message": "重复", "severity": "warn"}]}, failure_event_id=1
        )
        reporter.ingest_report_dict(
            {"findings": [{"rule": "L4", "file": "b.py", "message": "shell", "severity": "block"}]}, failure_event_id=1
        )
        top = reporter.top_issues()
        assert top[0]["rule"] == "L4"  # block 优先

    def test_categories(self):
        reporter = YagniAttributionReporter()
        reporter.ingest_report_dict(
            {"findings": [{"rule": "L2", "file": "a.py", "severity": "warn"}, {"rule": "L2", "file": "b.py", "severity": "warn"}]}
        )
        cat = reporter.by_category()
        assert cat["重复实现"] == 2

    def test_stats_and_clear(self):
        reporter = YagniAttributionReporter()
        reporter.ingest_report_dict({"findings": [{"rule": "L3", "file": "a.py", "severity": "info"}]})
        assert reporter.stats()["total_reports"] == 1
        assert reporter.stats()["unique_rules"] == 1
        reporter.clear()
        assert reporter.stats()["total_reports"] == 0

    def test_empty_reporter(self):
        reporter = YagniAttributionReporter()
        assert reporter.top_issues() == []
        assert reporter.stats()["total_findings"] == 0

    def test_integration_with_engine(self):
        """真实 YagniEngine → 归因报告器（端到端聚合）。"""
        engine = YagniEngine()
        reporter = YagniAttributionReporter()
        code = (
            "import os\nimport sys\n\ndef f(x):\n"
            "    if x:\n        if x:\n            if x:\n                if x:\n                    if x:\n                        return os.path.exists(x)\n"
            "    return False\n"
        )
        report = engine.validate("", code, "a.py")
        reporter.ingest(report, failure_event_id=5)
        top = reporter.top_issues()
        assert any(i["rule"] in ("L5", "L7") for i in top)
