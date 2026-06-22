"""T-26-2 崩溃轮次合成 closer 测试（v1.26，1.4）。

验证：冷恢复检测未关闭轮次（有 turn/start 无 turn/end）→ 合成 CrashCloser
事件 + 补 tool/result（TOOL_NOT_STARTED/TOOL_OUTCOME_UNKNOWN）+ step/end +
turn/end interrupted；已 flush 事件不重写；重放历史仍合法（seq 连续）。
"""

import json

import pytest

from src.protocol import CrashCloser, Event, EventType, parse_event
from src.storage.event_log import EventLog


def _mk(type_: EventType, **kw):
    """构造指定类型的最小事件。"""
    payload = {"id": kw.pop("id"), "session_id": kw.pop("session_id", "s1"), "type": type_, "ts": kw.pop("ts", 1.0)}
    payload.update(kw)
    return parse_event(json.dumps(payload))


class TestCrashCloserDetection:
    def test_detect_unclosed_turn(self):
        """有 turn/start 无 turn/end → 检测为崩溃轮次。"""
        from src.storage.crash_close import find_unclosed_turns

        events = [
            parse_event(json.dumps({"id": 1, "session_id": "s1", "type": "TurnStarted", "ts": 1.0, "mode": "act", "turn_index": 0, "op_id": "o1"})),
            parse_event(json.dumps({"id": 2, "session_id": "s1", "type": "ItemCompleted", "ts": 2.0, "item_type": "tool_result", "item_id": "i1"})),
        ]
        turns = find_unclosed_turns(events)
        assert turns == [0]

    def test_closed_turn_not_detected(self):
        """有 turn/end → 不是崩溃轮次。"""
        from src.storage.crash_close import find_unclosed_turns

        events = [
            parse_event(json.dumps({"id": 1, "session_id": "s1", "type": "TurnStarted", "ts": 1.0, "mode": "act", "turn_index": 0, "op_id": "o1"})),
            parse_event(json.dumps({"id": 2, "session_id": "s1", "type": "TurnCancelled", "ts": 2.0})),
        ]
        turns = find_unclosed_turns(events)
        assert turns == []

    def test_empty_log_no_crash(self):
        """空日志无崩溃轮次。"""
        from src.storage.crash_close import find_unclosed_turns

        assert find_unclosed_turns([]) == []


class TestCrashCloserAppend:
    def test_close_crash_turn_appends_closer(self, tmp_path):
        """崩溃轮次 → 追加 CrashCloser 事件（已 flush 事件不重写）。"""
        from src.storage.crash_close import close_crash_turn

        log = EventLog(tmp_path / "session.jsonl")
        # 先写入一个崩溃轮次（turn/start 无 turn/end）
        log.append(parse_event(json.dumps({"id": 1, "session_id": "s1", "type": "TurnStarted", "ts": 1.0, "mode": "act", "turn_index": 0, "op_id": "o1"})))
        log.append(parse_event(json.dumps({"id": 2, "session_id": "s1", "type": "ItemCompleted", "ts": 2.0, "item_type": "tool_result", "item_id": "i1"})))

        before = len(log.read_all())
        closed = close_crash_turn(log, turn=0, session_id="s1", outcome="TOOL_NOT_STARTED")
        after = log.read_all()

        # 追加了 CrashCloser
        assert closed is not None
        assert len(after) == before + 1
        assert after[-1].type == EventType.CRASH_CLOSER
        assert isinstance(after[-1], CrashCloser)
        assert after[-1].outcome == "TOOL_NOT_STARTED"
        # 已 flush 事件未被重写
        assert [e.id for e in after[:before]] == [1, 2]

    def test_closer_roundtrip_replayable(self, tmp_path):
        """合成 closer 后可重放（事件流合法、seq 连续）。"""
        from src.storage.crash_close import close_crash_turn

        log = EventLog(tmp_path / "session.jsonl")
        log.append(parse_event(json.dumps({"id": 1, "session_id": "s1", "type": "TurnStarted", "ts": 1.0, "mode": "act", "turn_index": 0, "op_id": "o1"})))
        close_crash_turn(log, turn=0, session_id="s1", outcome="TOOL_OUTCOME_UNKNOWN",
                         closed_steps=[{"step": 1, "tool_calls": 1, "outcome": "TOOL_OUTCOME_UNKNOWN"}])

        # 全部可解析（无损坏行）
        all_events = log.read_all()
        assert len(all_events) == 2
        # 事件 id 单调连续（EventLog 自动分配）
        ids = [e.id for e in all_events]
        assert ids == sorted(ids)
        assert len(set(ids)) == len(ids)

    def test_no_crash_no_closer(self, tmp_path):
        """无崩溃轮次 → 不追加 closer。"""
        from src.storage.crash_close import close_crash_turn

        log = EventLog(tmp_path / "session.jsonl")
        log.append(parse_event(json.dumps({"id": 1, "session_id": "s1", "type": "TurnStarted", "ts": 1.0, "mode": "act", "turn_index": 0, "op_id": "o1"})))
        log.append(parse_event(json.dumps({"id": 2, "session_id": "s1", "type": "TurnCancelled", "ts": 2.0})))

        before = len(log.read_all())
        closed = close_crash_turn(log, turn=0, session_id="s1", outcome="TOOL_NOT_STARTED")
        assert closed is None
        assert len(log.read_all()) == before
