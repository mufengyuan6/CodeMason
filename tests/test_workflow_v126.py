"""T-26-11 workflow 脚本编排测试（v1.26，G14——对标 DSH workflow/tool-ralph）。

验证：WorkflowEngine 执行编排脚本（agent() 调用 + phase 声明），
workflow/start|phase|log|end 事件全审计；meta 块（name/description/
phases）供 UI 展示；completed/cancelled/error 三态结束。
"""

import pytest

from src.loop.workflow import WorkflowEngine, WorkflowScript
from src.protocol import WorkflowEnd, WorkflowLog, WorkflowPhase, WorkflowStart


class TestWorkflowScript:
    def test_meta_block(self):
        """meta 块（name/description/whenToUse/phases）供 UI 列表展示。"""
        script = WorkflowScript(
            name="ci-cleanup",
            description="清理 CI 产物",
            when_to_use="CI 产物堆积时",
            phases=[{"title": "scan", "detail": "扫描产物"}, {"title": "clean", "detail": "清理"}],
        )
        assert script.name == "ci-cleanup"
        assert script.when_to_use == "CI 产物堆积时"
        assert len(script.phases) == 2
        assert script.phases[0]["title"] == "scan"

    def test_script_body(self):
        """脚本体（agent() 调用编排）。"""
        script = WorkflowScript(name="w1", description="", body="agent('scan')\nagent('fix')")
        assert script.body == "agent('scan')\nagent('fix')"


class TestWorkflowEngine:
    def test_start_emits_workflow_start(self):
        """run 开始 → workflow/start 事件（含 meta）。"""
        engine = WorkflowEngine()
        events = []
        script = WorkflowScript(name="w1", description="测试工作流", phases=[{"title": "p1"}])
        engine.run(script, on_event=events.append)
        starts = [e for e in events if isinstance(e, WorkflowStart)]
        assert len(starts) == 1
        assert starts[0].name == "w1"
        assert starts[0].phases[0]["title"] == "p1"

    def test_phase_events(self):
        """phase() 调用 → workflow/phase 事件。"""
        engine = WorkflowEngine()
        events = []
        script = WorkflowScript(name="w1", description="")
        # 手动调用 phase 推进
        engine.run(script, on_event=events.append)
        engine.enter_phase("scan", "扫描中", on_event=events.append)
        phases = [e for e in events if isinstance(e, WorkflowPhase)]
        assert len(phases) == 1
        assert phases[0].phase == "scan"
        assert phases[0].detail == "扫描中"

    def test_completed_end(self):
        """正常结束 → workflow/end {completed} + agent_calls 计数。"""
        engine = WorkflowEngine()
        events = []
        script = WorkflowScript(name="w1", description="")
        engine.run(script, on_event=events.append)
        engine.record_agent_call(on_event=events.append)
        engine.record_agent_call(on_event=events.append)
        engine.complete(on_event=events.append)
        ends = [e for e in events if isinstance(e, WorkflowEnd)]
        assert len(ends) == 1
        assert ends[0].stop_reason == "completed"
        assert ends[0].agent_calls == 2

    def test_error_end(self):
        """失败结束 → workflow/end {error} + 错误信息。"""
        engine = WorkflowEngine()
        events = []
        script = WorkflowScript(name="w1", description="")
        engine.run(script, on_event=events.append)
        engine.fail("脚本超时", on_event=events.append)
        ends = [e for e in events if isinstance(e, WorkflowEnd)]
        assert ends[0].stop_reason == "error"
        assert ends[0].error == "脚本超时"

    def test_log_events(self):
        """log() → workflow/log 事件。"""
        engine = WorkflowEngine()
        events = []
        script = WorkflowScript(name="w1", description="")
        engine.run(script, on_event=events.append)
        engine.log("产物 2 个超时", level="warn", on_event=events.append)
        logs = [e for e in events if isinstance(e, WorkflowLog)]
        assert len(logs) == 1
        assert logs[0].level == "warn"
        assert logs[0].message == "产物 2 个超时"

    def test_full_sequence(self):
        """完整序列：start → phase → log → agent → end 全事件审计。"""
        engine = WorkflowEngine()
        events = []
        script = WorkflowScript(name="w1", description="", phases=[{"title": "scan"}])
        engine.run(script, on_event=events.append)
        engine.enter_phase("scan", on_event=events.append)
        engine.record_agent_call(on_event=events.append)
        engine.log("agent 完成", on_event=events.append)
        engine.complete(on_event=events.append)
        types = [type(e).__name__ for e in events]
        assert types == ["WorkflowStart", "WorkflowPhase", "WorkflowLog", "WorkflowEnd"]
        # start 在 phase 前，end 最后
        assert types.index("WorkflowStart") < types.index("WorkflowPhase")
        assert types.index("WorkflowEnd") == len(types) - 1
