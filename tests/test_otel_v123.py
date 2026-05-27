"""G13 OTel 遥测导出测试（v1.22 落地）。

验收：
- 事件流订阅 → OTLP 记录（prompt/审批决策/工具结果/沙箱轨迹）
- 无端点优雅降级（本地记录，不崩溃）
- 导出失败不影响事件流主流程
- 快照导出（企业合规留档）
"""

import time

from src.observability.otel_exporter import OTelExporter
from src.protocol.events import (
    ClassifierVerdict,
    EventRecall,
    ExecApprovalRequest,
    ItemCompleted,
    TraceRecord,
    TurnStarted,
)
from src.storage import EventLog


def _seed_log(tmp_path) -> EventLog:
    log = EventLog(tmp_path / "otel.jsonl")
    return log


def _write_events(log: EventLog) -> None:
    """写入关注面事件（供 attach 后的回调消费）。"""
    eid = log.next_event_id
    evs = [
        TurnStarted(id=eid(), session_id="s1", mode="act", turn_index=1, op_id="op1", ts=100.0),
        ItemCompleted(id=eid(), session_id="s1", item_type="tool_result", item_id="t1", content={"path": "a.py"}, ts=101.0),
        ClassifierVerdict(id=eid(), session_id="s1", tool_name="Bash", command="rm -rf /", decision="block", reason="hard-deny", ts=102.0),
        TraceRecord(id=eid(), session_id="s1", trace_id="tr-1", executor="docker-sandbox", command="ls", exit_code=0, ts=103.0),
        EventRecall(id=eid(), session_id="s1", target_event_id=1, ts=104.0),  # 不应导出
    ]
    log.append_many(evs)


class TestOTelExporter:
    def test_attach_converts_events(self, tmp_path):
        """事件流订阅 → 转换 → 本地记录（无端点降级）。"""
        log = _seed_log(tmp_path)
        exporter = OTelExporter()  # 无端点 → 本地降级
        exporter.attach(log)
        _write_events(log)  # attach 后写入才触发回调
        records = exporter.local_log()
        names = {r["name"] for r in records}
        assert "user.prompt" in names          # prompt
        assert "approval.decision" in names    # 审批决策
        assert "tool.tool_result" in names     # 工具结果
        assert "sandbox.trace" in names        # 沙箱轨迹
        assert "event_recall" not in names     # 非关注事件不导出（防泛滥）
        exporter.detach()

    def test_no_endpoint_graceful(self, tmp_path):
        """无 OTLP 端点 → 优雅降级（本地记录，不崩溃）。"""
        log = _seed_log(tmp_path)
        exporter = OTelExporter(local_fallback=True)
        exporter.attach(log)
        _write_events(log)
        assert exporter.stats()["local_records"] >= 4
        assert exporter.stats()["endpoint"] is None
        exporter.detach()

    def test_export_failure_does_not_break_eventflow(self, tmp_path):
        """导出失败不影响事件流（遥测失败绝不阻断主流程）。"""
        log = _seed_log(tmp_path)
        exporter = OTelExporter()
        original = exporter._convert
        exporter._convert = lambda e: (_ for _ in ()).throw(RuntimeError("collector down"))  # 转换抛异常
        exporter.attach(log)
        _write_events(log)  # 异常被 _on_event 吞掉
        exporter._convert = original
        # 事件流仍正常
        assert len(log.read_all()) >= 5
        exporter.detach()

    def test_approval_decision_attribute(self, tmp_path):
        """审批决策带 decision 属性（Compliance Platform 关注面）。"""
        log = _seed_log(tmp_path)
        exporter = OTelExporter()
        exporter.attach(log)
        _write_events(log)
        approvals = [r for r in exporter.local_log() if r["name"] == "approval.decision"]
        assert approvals
        assert approvals[0]["attributes"]["decision"] == "block"
        exporter.detach()

    def test_snapshot_export(self, tmp_path):
        """快照导出 JSONL（企业合规留档）。"""
        log = _seed_log(tmp_path)
        exporter = OTelExporter()
        exporter.attach(log)
        _write_events(log)
        out = tmp_path / "telemetry.jsonl"
        exporter.export_snapshot(str(out))
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 4
        exporter.detach()

    def test_detach_stops_subscription(self, tmp_path):
        """detach 后不再接收事件。"""
        log = _seed_log(tmp_path)
        exporter = OTelExporter()
        exporter.attach(log)
        exporter.detach()
        before = exporter.stats()["exported_count"]
        # 再写事件不应被导出
        log.append(ItemCompleted(id=log.next_event_id(), session_id="s1", item_type="tool_result", item_id="x", ts=time.time()))
        assert exporter.stats()["exported_count"] == before

    def test_stats(self, tmp_path):
        log = _seed_log(tmp_path)
        exporter = OTelExporter(endpoint="http://collector:4318")
        exporter.attach(log)
        _write_events(log)
        stats = exporter.stats()
        assert stats["attached"] is True
        assert stats["endpoint"] == "http://collector:4318"
        assert stats["exported_count"] >= 4
        exporter.detach()
