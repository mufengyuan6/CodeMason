"""G17⑧ AI 贡献报告测试（v1.21 验收口径）。

验收标准：
- 任意任务可导出 ContributionReport（零 LLM、纯事件投影）
- 与事件流重算一致（可复算：同输入同输出）
- 变更归属标注 100% 进 git/PR（metadata 生成）
- 报告可下钻 provenance 事件链
"""

import time

from src.projection.contribution import ContributionReporter
from src.protocol.events import ExecApprovalRequest, ItemCompleted, TurnStarted
from src.protocol.ops import UserTurnStart
from src.storage import EventLog


def _seed_events(tmp_path, *, human_intervention: bool = False) -> EventLog:
    """构造事件流：一轮任务 + 文件写入 + （可选）人工审批介入。"""
    log = EventLog(tmp_path / "events.jsonl")
    eid = log.next_event_id

    evs = [
        TurnStarted(id=eid(), session_id="s1", mode="act", turn_index=1, op_id="op1", ts=100.0),
        ItemCompleted(
            id=eid(), session_id="s1", item_type="tool_result",
            item_id="Write-1",
            content={"path": "src/feature.py", "line_range": [1, 40], "tokens": 1200},
            ts=102.0,
        ),
        ItemCompleted(
            id=eid(), session_id="s1", item_type="tool_result",
            item_id="Write-2",
            content={"path": "tests/test_feature.py", "line_range": [1, 25], "tokens": 800},
            ts=104.0,
        ),
    ]
    if human_intervention:
        evs.append(
            ExecApprovalRequest(
                id=eid(), session_id="s1", approval_id="a1", tool_name="Bash",
                description="执行命令", command="rm -rf /tmp/x", risk_level="red", ts=103.0,
            )
        )
    log.append_many(evs)
    return log


class TestContributionReporter:
    def test_build_report_files(self, tmp_path):
        """文件贡献提取：path + line_range + provenance 事件链。"""
        log = _seed_events(tmp_path)
        report = ContributionReporter(log).build(task_id="t1")
        paths = {f.path for f in report.files}
        assert "src/feature.py" in paths
        assert "tests/test_feature.py" in paths
        feature = [f for f in report.files if f.path == "src/feature.py"][0]
        assert feature.line_range == [1, 40]
        assert len(feature.provenance_event_ids) >= 1  # 可下钻

    def test_full_auto_involvement(self, tmp_path):
        """无人工介入 → full_auto。"""
        log = _seed_events(tmp_path)
        report = ContributionReporter(log).build(task_id="t1")
        assert report.ai_involvement == "full_auto"

    def test_assisted_involvement_with_human(self, tmp_path):
        """人工审批介入 → assisted。"""
        log = _seed_events(tmp_path, human_intervention=True)
        report = ContributionReporter(log).build(task_id="t1")
        assert report.ai_involvement == "assisted"

    def test_cost_collected(self, tmp_path):
        """成本投影：事件流 ts 跨度 + token 汇总。"""
        log = _seed_events(tmp_path)
        report = ContributionReporter(log).build(task_id="t1")
        assert report.cost["tokens"] == 2000  # 1200 + 800
        assert report.cost["duration_s"] >= 4.0  # 104 - 100

    def test_deterministic_rebuild(self, tmp_path):
        """纯投影可复算：同事件流两次 build → 同哈希。"""
        log = _seed_events(tmp_path)
        r1 = ContributionReporter(log).build(task_id="t1")
        r2 = ContributionReporter(log).build(task_id="t1")
        assert ContributionReporter.content_hash(r1) == ContributionReporter.content_hash(r2)

    def test_git_attribution_metadata(self, tmp_path):
        """变更归属标注：git commit metadata（AI 写的就要标出来）。"""
        log = _seed_events(tmp_path)
        report = ContributionReporter(log).build(task_id="t1")
        meta = ContributionReporter.git_attribution_metadata(report)
        assert "CodeMason AI Agent" in meta["Co-Authored-By"]
        assert meta["X-CodeMason-Involvement"] == "full_auto"
        assert "src/feature.py" in meta["X-CodeMason-Files"]
        assert meta["X-CodeMason-Verification"] == "unknown"

    def test_empty_log(self, tmp_path):
        """空事件流 → 空报告不崩溃。"""
        log = EventLog(tmp_path / "empty.jsonl")
        report = ContributionReporter(log).build(task_id="t1")
        assert report.files == []
        assert report.ai_involvement == "human_led"
        assert report.cost == {"tokens": 0, "duration_s": 0.0}
