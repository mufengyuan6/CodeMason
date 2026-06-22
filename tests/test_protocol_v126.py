"""T-26-12 协议层扩展测试（v1.26：9 类新事件）。

验证：Retry/RetryStarted/CrashCloser/GoalChange/WorkflowStart/WorkflowPhase/
WorkflowLog/WorkflowEnd/PermissionPresetSelected 9 类事件可构造、可序列化、
可反序列化（判别联合），EventUnion 覆盖全部新类型。
"""

import pytest

from src.protocol import (
    CrashCloser,
    Event,
    GoalChange,
    PermissionPresetSelected,
    Retry,
    RetryStarted,
    WorkflowEnd,
    WorkflowLog,
    WorkflowPhase,
    WorkflowStart,
    event_to_json,
    parse_event,
)


def _roundtrip(event: Event) -> Event:
    """序列化→反序列化往返，返回反序列化结果。"""
    raw = event_to_json(event)
    return parse_event(raw)


class TestRetryEvent:
    def test_retry_roundtrip(self):
        ev = Retry(id=1, session_id="s1", retry_id="r1", turn=2, step=1,
                   provider="xf-yun", policy_key='["always",1.0,60.0,0.5]',
                   retry=2, max_retries=5, delay_ms=1500.0,
                   failure="429 Rate Limit", op_id="op1", ts=1.0)
        parsed = _roundtrip(ev)
        assert parsed.type.value == "Retry"
        assert parsed.retry_id == "r1"
        assert parsed.policy_key == '["always",1.0,60.0,0.5]'
        assert parsed.retry == 2
        assert parsed.delay_ms == 1500.0

    def test_retry_started_roundtrip(self):
        ev = RetryStarted(id=2, session_id="s1", retry_id="r1", turn=2, step=1, retry=2, ts=1.0)
        parsed = _roundtrip(ev)
        assert parsed.type.value == "RetryStarted"
        assert parsed.retry_id == "r1"
        assert parsed.retry == 2


class TestCrashCloserEvent:
    def test_crash_closer_roundtrip(self):
        ev = CrashCloser(id=3, session_id="s1", turn=5,
                         closed_steps=[{"step": 1, "tool_calls": 1, "outcome": "TOOL_OUTCOME_UNKNOWN"}],
                         outcome="TOOL_OUTCOME_UNKNOWN", ts=1.0)
        parsed = _roundtrip(ev)
        assert parsed.type.value == "CrashCloser"
        assert parsed.turn == 5
        assert parsed.outcome == "TOOL_OUTCOME_UNKNOWN"
        assert parsed.closed_steps[0]["outcome"] == "TOOL_OUTCOME_UNKNOWN"

    def test_crash_closer_empty_outcome(self):
        ev = CrashCloser(id=4, session_id="s1", turn=6, closed_steps=[], outcome="EMPTY", ts=1.0)
        parsed = _roundtrip(ev)
        assert parsed.outcome == "EMPTY"


class TestGoalChangeEvent:
    def test_goal_create_roundtrip(self):
        ev = GoalChange(id=5, session_id="s1", operation="create",
                        goal={"id": "g1", "objective": "修复登录 bug", "status": "active",
                              "revision": 1, "createdAt": 1000, "updatedAt": 1000},
                        rounds_started=0, revision=1, ts=1.0)
        parsed = _roundtrip(ev)
        assert parsed.type.value == "GoalChange"
        assert parsed.operation == "create"
        assert parsed.goal["objective"] == "修复登录 bug"
        assert parsed.revision == 1

    def test_goal_clear_tombstone(self):
        ev = GoalChange(id=6, session_id="s1", operation="clear",
                        goal=None, cleared_goal_id="g1", rounds_started=3, revision=1, ts=1.0)
        parsed = _roundtrip(ev)
        assert parsed.operation == "clear"
        assert parsed.cleared_goal_id == "g1"
        assert parsed.goal is None

    def test_goal_edit_increments_revision(self):
        ev = GoalChange(id=7, session_id="s1", operation="edit",
                        goal={"id": "g1", "objective": "修复登录 bug（含 2FA）", "status": "active",
                              "revision": 2, "createdAt": 1000, "updatedAt": 2000},
                        rounds_started=1, revision=2, ts=1.0)
        parsed = _roundtrip(ev)
        assert parsed.operation == "edit"
        assert parsed.revision == 2


class TestWorkflowEvents:
    def test_workflow_start_roundtrip(self):
        ev = WorkflowStart(id=8, session_id="s1", workflow_run_id="wf1",
                           name="ci-cleanup", description="清理 CI 产物",
                           phases=[{"title": "scan", "detail": "扫描产物"}], ts=1.0)
        parsed = _roundtrip(ev)
        assert parsed.type.value == "WorkflowStart"
        assert parsed.name == "ci-cleanup"
        assert parsed.phases[0]["title"] == "scan"

    def test_workflow_phase_roundtrip(self):
        ev = WorkflowPhase(id=9, session_id="s1", workflow_run_id="wf1",
                           phase="scan", detail="扫描产物", ts=1.0)
        parsed = _roundtrip(ev)
        assert parsed.phase == "scan"

    def test_workflow_log_roundtrip(self):
        ev = WorkflowLog(id=10, session_id="s1", workflow_run_id="wf1",
                         level="warn", message="产物 2 个超时", ts=1.0)
        parsed = _roundtrip(ev)
        assert parsed.level == "warn"
        assert parsed.message == "产物 2 个超时"

    def test_workflow_end_roundtrip(self):
        ev = WorkflowEnd(id=11, session_id="s1", workflow_run_id="wf1",
                         stop_reason="completed", agent_calls=3, ts=1.0)
        parsed = _roundtrip(ev)
        assert parsed.stop_reason == "completed"
        assert parsed.agent_calls == 3

    def test_workflow_end_error(self):
        ev = WorkflowEnd(id=12, session_id="s1", workflow_run_id="wf1",
                         stop_reason="error", agent_calls=1, error="脚本超时", ts=1.0)
        parsed = _roundtrip(ev)
        assert parsed.stop_reason == "error"
        assert parsed.error == "脚本超时"


class TestPermissionPresetEvent:
    def test_preset_selected_roundtrip(self):
        ev = PermissionPresetSelected(id=13, session_id="s1", preset_name="workspace-write",
                                      sandbox_mode="workspace-write", approval_policy="ask", ts=1.0)
        parsed = _roundtrip(ev)
        assert parsed.type.value == "PermissionPresetSelected"
        assert parsed.preset_name == "workspace-write"
        assert parsed.approval_policy == "ask"

    def test_preset_custom(self):
        ev = PermissionPresetSelected(id=14, session_id="s1", preset_name="custom",
                                      sandbox_mode="readonly", approval_policy="never", ts=1.0)
        parsed = _roundtrip(ev)
        assert parsed.preset_name == "custom"
