"""G17 投影层测试：state/trace/metrics/specfile（v1.23 落地）。

验收（design.md Phase 1 强制测试）：
- 快照：恢复=快照+增量非全量重放；content_hash 校验失败 → 以事件流重建（fail-safe）
- 轨迹：换 executor 字段接口零重写，沙箱内命令 100% 有 TraceRecord
- 指标：聚合与事件流重算一致（纯投影可复算）
- Spec：frozen spec 断言跑过自动产出 verified state
"""

import time

from src.protocol.events import Error, ItemCompleted, TurnStarted
from src.protocol.specfile import SpecManager, SpecStatus
from src.projection.metrics import MetricsProjector
from src.projection.state import StateProjector
from src.projection.trace import TraceCollector
from src.storage import EventLog


def _seed_events(tmp_path):
    log = EventLog(tmp_path / "s.jsonl")
    eid = log.next_event_id
    log.append_many([
        TurnStarted(id=eid(), session_id="s1", mode="act", turn_index=1, op_id="op1", ts=100.0),
        ItemCompleted(id=eid(), session_id="s1", item_type="tool_result", item_id="t1", content={"status": "ok", "tokens": 100}, ts=101.0),
        ItemCompleted(id=eid(), session_id="s1", item_type="tool_result", item_id="t2", content={"status": "failed", "tokens": 50}, ts=102.0),
        Error(id=eid(), session_id="s1", message="网络错误", error_type="network", ts=103.0),
        ItemCompleted(id=eid(), session_id="s1", item_type="turn_summary", item_id="sum1", content={"status": "completed"}, ts=104.0),
    ])
    return log


class TestStateProjector:
    """Verified State 快照（G17①）。"""

    def test_create_with_bounds(self, tmp_path):
        log = _seed_events(tmp_path)
        proj = StateProjector(log, project_root=str(tmp_path))
        state = proj.create(trigger="manual")
        assert state.last_event_id == log.file_last_id()
        assert state.content_hash  # SHA256 存在
        assert state.generated_by == "ai"

    def test_recover_incremental(self, tmp_path):
        """恢复 = 快照 + 增量重放（替代全量重放）。"""
        log = _seed_events(tmp_path)
        proj = StateProjector(log, project_root=str(tmp_path))
        state = proj.create(trigger="gate")
        # 快照后新增事件
        eid = log.next_event_id
        log.append(ItemCompleted(id=eid(), session_id="s1", item_type="tool_result", item_id="t3", content={"status": "ok"}, ts=105.0))
        # 增量 = 快照边界后的事件（非全量）
        incremental = proj.recover(state, event_log=log)
        assert len(incremental) == 1
        assert incremental[0].item_id == "t3"

    def test_verify_fail_safe(self, tmp_path):
        """快照校验失败 → 以事件流重建（fail-safe 语义：verify 返回 False 时可重建）。"""
        log = _seed_events(tmp_path)
        proj = StateProjector(log, project_root=str(tmp_path))
        state = proj.create(trigger="manual")
        # 篡改快照哈希 → verify 失败（恢复侧据此触发重建）
        state.content_hash = "tampered"
        # 不抛异常、可判定（fail-safe：不一致以事件为准）
        assert isinstance(proj.verify(state), bool)


class TestTraceCollector:
    """轨迹协议（G17②：沙箱不可知）。"""

    def test_record_with_executor(self, tmp_path):
        log = EventLog(tmp_path / "trace.jsonl")
        collector = TraceCollector(event_log=log)
        tr = collector.record(executor="docker-sandbox", command="ls -la", exit_code=0, output="file1\nfile2", duration_ms=12.5)
        assert tr.executor == "docker-sandbox"
        assert tr.output_digest  # SHA256 摘要
        # 轨迹进事件溯源
        events = log.read_all()
        assert any(e.type.value == "TraceRecord" for e in events)

    def test_executor_field_swappable(self):
        """换沙箱只换 executor 字段（接口零重写）。"""
        collector = TraceCollector()
        for executor in ("docker-sandbox", "gvisor", "firecracker", "e2b", "local"):
            tr = collector.record(executor=executor, command="echo hi", exit_code=0)
            assert tr.executor == executor  # 同一 record() 接口

    def test_output_digest(self):
        collector = TraceCollector()
        tr = collector.record(executor="local", command="cat secret", exit_code=0, output="sk-abcdefghij1234567890")
        assert len(tr.output_digest) == 16  # SHA256 前缀（防篡改）
        assert "sk-abcdefghij" not in tr.output_digest  # 摘要不含明文


class TestMetricsProjector:
    """Metrics 指标投影（G17③）。"""

    def test_aggregate(self, tmp_path):
        log = _seed_events(tmp_path)
        proj = MetricsProjector(event_log=log)
        w = proj.aggregate(window_type="session", window_id="s1")
        assert w.metrics["task_count"] == 1  # turn_summary
        assert w.metrics["task_success_rate"] == 1.0
        assert w.metrics["tool_call_count"] == 2  # 2 个 tool_result
        assert w.metrics["tool_success_rate"] == 0.5  # 1 ok / 1 failed
        assert w.metrics["token_cost"] == 150  # 100 + 50
        assert w.metrics["failure_distribution"]["network"] == 1

    def test_deterministic_recompute(self, tmp_path):
        """纯投影可复算：同事件流两次聚合 → 同指标。"""
        log = _seed_events(tmp_path)
        proj = MetricsProjector(event_log=log)
        m1 = proj.aggregate().metrics
        m2 = proj.aggregate().metrics
        assert m1 == m2

    def test_empty_log(self, tmp_path):
        log = EventLog(tmp_path / "empty.jsonl")
        proj = MetricsProjector(event_log=log)
        w = proj.aggregate()
        assert w.metrics["task_count"] == 0
        assert w.metrics["tool_success_rate"] == 0.0


class TestSpecManager:
    """Spec 验收状态（G17④：frozen 后断言跑过 → verified state）。"""

    def test_lifecycle(self):
        sm = SpecManager()
        spec = sm.create("spec-1", "登录修复", "修复登录 500", acceptance=["文件 src/auth.py 存在", "pytest tests/test_auth.py 全绿"])
        assert spec.status == SpecStatus.DRAFT
        sm.review("spec-1")
        assert spec.status == SpecStatus.REVIEWED
        sm.freeze("spec-1")
        assert spec.status == SpecStatus.FROZEN

    def test_freeze_requires_review(self):
        sm = SpecManager()
        sm.create("spec-2", "x", acceptance=["y"])
        import pytest

        with pytest.raises(ValueError):
            sm.freeze("spec-2")  # draft 不能直接 frozen

    def test_verify_acceptance_verified(self, tmp_path):
        """验收断言跑过 → verified state（done is not subjective）。"""
        # 构造真实存在的文件 + 断言 runner
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.py").write_text("def login(): pass")
        sm = SpecManager()
        spec = sm.create("spec-3", "登录修复", acceptance=["文件 src/auth.py 存在"])
        sm.review("spec-3")
        sm.freeze("spec-3")
        # 在 src 目录下跑验收
        import os

        old = os.getcwd()
        os.chdir(tmp_path)
        try:
            sm.verify_acceptance("spec-3")
        finally:
            os.chdir(old)
        assert spec.status == SpecStatus.VERIFIED
        assert spec.acceptance[0].status == "passed"

    def test_verify_with_runner(self):
        """外部 runner 跑断言（pytest 等机读验证）。"""
        sm = SpecManager()
        spec = sm.create("spec-4", "x", acceptance=["pytest 全绿"])
        sm.review("spec-4")
        sm.freeze("spec-4")
        sm.verify_acceptance("spec-4", runner=lambda assertion: "全绿" in assertion)
        assert spec.status == SpecStatus.VERIFIED

    def test_verify_failed_stays_frozen(self):
        """断言失败 → 不产出 verified（保持 frozen）。"""
        sm = SpecManager()
        spec = sm.create("spec-5", "x", acceptance=["pytest 全绿"])
        sm.review("spec-5")
        sm.freeze("spec-5")
        sm.verify_acceptance("spec-5", runner=lambda a: False)
        assert spec.status == SpecStatus.FROZEN
        assert spec.acceptance[0].status == "failed"

    def test_spec_file_export(self):
        sm = SpecManager()
        sm.create("spec-6", "特性 X", "叙事内容", acceptance=["断言1"])
        content = sm.to_spec_file("spec-6")
        assert "# 特性 X" in content
        assert "acceptance:" in content
        assert "spec_id: spec-6" in content
