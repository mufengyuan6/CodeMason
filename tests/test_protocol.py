"""Phase 1 测试：协议层（Op/Event 双向契约）。"""

import pytest
from pydantic import ValidationError

from src.protocol import (
    PROTOCOL_VERSION,
    EventType,
    OpType,
    event_to_json,
    op_to_json,
    parse_event,
    parse_op,
)
from src.protocol.events import AgentMessageContentDelta, ExecApprovalRequest, TurnStarted
from src.protocol.ops import ApprovalResponse, Compact, UserTurnCancel, UserTurnStart


class TestOpProtocol:
    def test_user_turn_start_roundtrip(self):
        op = UserTurnStart(content="修复 bug", mode="act")
        raw = op_to_json(op)
        parsed = parse_op(raw)
        assert isinstance(parsed, UserTurnStart)
        assert parsed.content == "修复 bug"
        assert parsed.mode == "act"
        assert parsed.protocol_version == PROTOCOL_VERSION

    def test_op_discriminated_union(self):
        """判别联合：不同 type 解析为不同 Op 类。"""
        ops = [
            UserTurnStart(content="x"),
            UserTurnCancel(reason="测试"),
            ApprovalResponse(approval_id="a1", decision="approve"),
            Compact(target="context"),
        ]
        for op in ops:
            parsed = parse_op(op_to_json(op))
            assert type(parsed) is type(op), f"{type(op)} → {type(parsed)}"

    def test_op_idempotency_id_unique(self):
        """Op 幂等 id：每次生成唯一。"""
        a = UserTurnStart(content="x")
        b = UserTurnStart(content="x")
        assert a.op_id != b.op_id

    def test_approval_edit_requires_command(self):
        """edit 决策带 edited_command 校验。"""
        op = ApprovalResponse(approval_id="a1", decision="edit", edited_command="ls -la")
        assert op.edited_command == "ls -la"
        # 未提供 edited_command 也合法（由内核判断）
        op2 = ApprovalResponse(approval_id="a1", decision="edit")
        assert op2.edited_command is None

    def test_unknown_op_type_rejected(self):
        with pytest.raises(ValidationError):
            parse_op({"type": "NotAnOp", "op_id": "x", "protocol_version": "v1"})

    def test_protocol_version_validation(self):
        with pytest.raises(ValueError):
            parse_op({"type": "UserTurnStart", "content": "x", "protocol_version": "v9"})


class TestEventProtocol:
    def test_turn_started_frozen(self):
        """Event 不可变（范式声明 frozen=True）。"""
        ev = TurnStarted(id=1, session_id="s1", mode="act", turn_index=1, op_id="o1", ts=1.0)
        with pytest.raises(ValidationError):
            ev.id = 99  # frozen 禁止修改

    def test_event_discriminated_union(self):
        events = [
            TurnStarted(id=1, session_id="s", mode="act", turn_index=1, op_id="o", ts=1.0),
            AgentMessageContentDelta(id=2, session_id="s", message_index=0, delta="hi", ts=1.0),
            ExecApprovalRequest(
                id=3, session_id="s", approval_id="a1", tool_name="Bash",
                command="rm -rf /", description="高危删除", risk_level="red", ts=1.0,
            ),
        ]
        for ev in events:
            parsed = parse_event(event_to_json(ev))
            assert type(parsed) is type(ev)

    def test_approval_request_risk_levels(self):
        for level in ("red", "yellow", "green"):
            ev = ExecApprovalRequest(
                id=1, session_id="s", approval_id="a", tool_name="Bash",
                command="ls", description="列出目录", risk_level=level, ts=1.0,
            )
            assert ev.risk_level == level

    def test_invalid_risk_level_rejected(self):
        with pytest.raises(ValidationError):
            ExecApprovalRequest(
                id=1, session_id="s", approval_id="a", tool_name="Bash",
                command="ls", risk_level="blue", ts=1.0,
            )

    def test_message_delta_complete_flag(self):
        ev = AgentMessageContentDelta(id=1, session_id="s", message_index=0, delta="", complete=True, ts=1.0)
        assert ev.complete is True

    def test_all_event_types_serializable(self):
        """所有 EventType 均可序列化（协议完整性）。"""
        from src.protocol.events import Error as ErrorEvent
        from src.protocol.events import ItemCompleted, Rollback, TurnCancelled

        evs = [
            TurnStarted(id=1, session_id="s", mode="act", turn_index=1, op_id="o", ts=1.0),
            AgentMessageContentDelta(id=2, session_id="s", message_index=0, delta="d", ts=1.0),
            ExecApprovalRequest(id=3, session_id="s", approval_id="a", tool_name="Bash", command="c", description="命令", risk_level="red", ts=1.0),
            ItemCompleted(id=4, session_id="s", item_type="tool_result", item_id="i", ts=1.0),
            TurnCancelled(id=5, session_id="s", reason="r", ts=1.0),
            ErrorEvent(id=6, session_id="s", message="m", ts=1.0),
            Rollback(id=7, session_id="s", checkpoint_id="c1", ts=1.0),
        ]
        for ev in evs:
            parsed = parse_event(event_to_json(ev))
            assert type(parsed) is type(ev)


class TestV113NewEvents:
    """v1.13 新增事件（inject/condensation/stale/recall）协议完整性。"""

    def test_inject_event_roundtrip(self):
        """注入即事件：记忆注入审计基础设施。"""
        from src.protocol.events import Inject

        ev = Inject(
            id=10, session_id="s1", memory_id="mem-3", task_id="task-7",
            confidence=0.8, source_trust="user_confirmed", position="head",
            chars=512, ts=1.0,
        )
        parsed = parse_event(event_to_json(ev))
        assert isinstance(parsed, Inject)
        assert parsed.memory_id == "mem-3"
        assert parsed.source_trust == "user_confirmed"
        assert parsed.position == "head"

    def test_inject_defaults(self):
        """注入事件默认值：agent_inferred + 空 task。"""
        from src.protocol.events import Inject

        ev = Inject(id=1, session_id="s", memory_id="m1", ts=1.0)
        assert ev.source_trust == "agent_inferred"
        assert ev.task_id == ""
        assert ev.confidence == 0.0

    def test_condensation_event_roundtrip(self):
        """压缩即事件：覆盖范围 + 策略参数 + 验证结果。"""
        from src.protocol.events import Condensation

        ev = Condensation(
            id=11, session_id="s1", policy_version="ab-lambda-01",
            first_event_id=1, last_event_id=88,
            params={"lambda": 0.1, "trigger": 0.6, "keep_recent": 6},
            tokens_before=52000, tokens_after=8000, verified=True, ts=1.0,
        )
        parsed = parse_event(event_to_json(ev))
        assert isinstance(parsed, Condensation)
        assert parsed.policy_version == "ab-lambda-01"
        assert parsed.params["lambda"] == 0.1
        assert parsed.tokens_after == 8000
        assert parsed.verified is True

    def test_event_stale_roundtrip(self):
        """事件级失效：file_changed → 旧 tool_result 标 stale。"""
        from src.protocol.events import EventStale

        ev = EventStale(
            id=12, session_id="s1", file_path="src/foo.py",
            change_event_id=42, stale_event_ids=[10, 11], ts=1.0,
        )
        parsed = parse_event(event_to_json(ev))
        assert isinstance(parsed, EventStale)
        assert parsed.file_path == "src/foo.py"
        assert 10 in parsed.stale_event_ids

    def test_event_recall_roundtrip(self):
        """回读记录：压缩质量信号。"""
        from src.protocol.events import EventRecall

        ev = EventRecall(
            id=13, session_id="s1", target_event_id=88,
            via="event_search", query="BudgetAllocator", reason="compressed_recovery", ts=1.0,
        )
        parsed = parse_event(event_to_json(ev))
        assert isinstance(parsed, EventRecall)
        assert parsed.via == "event_search"
        assert parsed.query == "BudgetAllocator"

    def test_v113_event_types_serializable(self):
        """全部 v1.13 事件类型可序列化（判别联合完整性）。"""
        from src.protocol.events import Condensation, EventRecall, EventStale, Inject

        evs = [
            Inject(id=1, session_id="s", memory_id="m", ts=1.0),
            Condensation(id=2, session_id="s", first_event_id=1, last_event_id=5, ts=1.0),
            EventStale(id=3, session_id="s", file_path="f.py", change_event_id=2, ts=1.0),
            EventRecall(id=4, session_id="s", target_event_id=2, ts=1.0),
        ]
        for ev in evs:
            parsed = parse_event(event_to_json(ev))
            assert type(parsed) is type(ev)


class TestV123NewEvents:
    """v1.23 落地新增事件（ClassifierVerdict/TraceRecord/WriteLock/SnapshotCreated）协议完整性。"""

    def test_classifier_verdict_roundtrip(self):
        """分类器判决即事件：allow/block/escalate + 理由 + 置信度。"""
        from src.protocol.events import ClassifierVerdict

        ev = ClassifierVerdict(
            id=20, session_id="s1", tool_name="Bash", command="rm -rf /",
            decision="block", reason="hard-deny: 破坏性删除根路径", tier=3,
            confidence=0.97, stage="stage2", op_id="op-1", ts=1.0,
        )
        parsed = parse_event(event_to_json(ev))
        assert isinstance(parsed, ClassifierVerdict)
        assert parsed.decision == "block"
        assert parsed.confidence == 0.97
        assert parsed.tier == 3

    def test_classifier_verdict_alternative(self):
        """safer-alternative 处置：建议安全替代命令。"""
        from src.protocol.events import ClassifierVerdict

        ev = ClassifierVerdict(
            id=21, session_id="s1", tool_name="Bash", command="git push --force",
            decision="alternative", reason="危险强推，建议 --force-with-lease",
            suggested_alternative="git push --force-with-lease", ts=1.0,
        )
        parsed = parse_event(event_to_json(ev))
        assert parsed.decision == "alternative"
        assert parsed.suggested_alternative == "git push --force-with-lease"

    def test_trace_record_roundtrip(self):
        """轨迹记录：沙箱不可知（换 executor 只换字段）。"""
        from src.protocol.events import TraceRecord

        ev = TraceRecord(
            id=22, session_id="s1", trace_id="tr-1", executor="docker-sandbox",
            command="ls -la", argv=["ls", "-la"], exit_code=0,
            output_digest="abc123", duration_ms=12.5, sandbox_id="sbx-1", ts=1.0,
        )
        parsed = parse_event(event_to_json(ev))
        assert isinstance(parsed, TraceRecord)
        assert parsed.executor == "docker-sandbox"
        assert parsed.exit_code == 0

    def test_write_lock_events_roundtrip(self):
        """Team Kernel 单写者锁：授予/释放配对。"""
        from src.protocol.events import WriteLockGranted, WriteLockReleased

        g = WriteLockGranted(id=23, session_id="s1", agent_id="agent-a", lock_id="lk-1", scope="session", ts=1.0)
        r = WriteLockReleased(id=24, session_id="s1", agent_id="agent-a", lock_id="lk-1", duration_s=2.5, ts=2.0)
        pg = parse_event(event_to_json(g))
        pr = parse_event(event_to_json(r))
        assert isinstance(pg, WriteLockGranted)
        assert isinstance(pr, WriteLockReleased)
        assert pg.lock_id == pr.lock_id == "lk-1"

    def test_snapshot_created_roundtrip(self):
        """投影层快照：边界 + content_hash + 文件清单。"""
        from src.protocol.events import SnapshotCreated

        ev = SnapshotCreated(
            id=25, session_id="s1", snapshot_id="snap-1",
            first_event_id=1, last_event_id=88, content_hash="sha256:xxx",
            trigger="gate", files=[{"path": "a.py", "sha256": "h1", "status": "modified"}], ts=1.0,
        )
        parsed = parse_event(event_to_json(ev))
        assert isinstance(parsed, SnapshotCreated)
        assert parsed.content_hash == "sha256:xxx"
        assert parsed.files[0]["path"] == "a.py"

    def test_v123_event_types_serializable(self):
        """全部 v1.23 新事件类型可序列化（判别联合完整性）。"""
        from src.protocol.events import (
            ClassifierVerdict,
            SnapshotCreated,
            TraceRecord,
            WriteLockGranted,
            WriteLockReleased,
        )

        evs = [
            ClassifierVerdict(id=30, session_id="s", tool_name="Bash", decision="allow", ts=1.0),
            TraceRecord(id=31, session_id="s", trace_id="t", executor="local", ts=1.0),
            WriteLockGranted(id=32, session_id="s", agent_id="a", lock_id="l", ts=1.0),
            WriteLockReleased(id=33, session_id="s", agent_id="a", lock_id="l", ts=1.0),
            SnapshotCreated(id=34, session_id="s", snapshot_id="sn", first_event_id=1, last_event_id=2, content_hash="h", ts=1.0),
        ]
        for ev in evs:
            parsed = parse_event(event_to_json(ev))
            assert type(parsed) is type(ev)
