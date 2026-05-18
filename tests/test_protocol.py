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
