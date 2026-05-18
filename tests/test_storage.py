"""Phase 1 测试：事件存储（JSONL + flock + 游标补发）。"""

import time

import pytest

from src.protocol.events import ItemCompleted, TurnStarted
from src.storage import EventLog, TailWatcher


def make_event(event_id: int, session: str = "s1") -> TurnStarted:
    return TurnStarted(id=event_id, session_id=session, mode="act", turn_index=event_id, op_id=f"o{event_id}", ts=time.time())


class TestEventLog:
    def test_append_and_read_roundtrip(self, tmp_path):
        log = EventLog(tmp_path / "events.jsonl")
        ev = make_event(1)
        log.append(ev)
        events = log.read_all()
        assert len(events) == 1
        assert events[0].id == 1
        assert events[0].session_id == "s1"

    def test_append_many_preserves_order(self, tmp_path):
        log = EventLog(tmp_path / "events.jsonl")
        evs = [make_event(i) for i in range(1, 6)]
        ids = log.append_many(evs)
        assert ids == [1, 2, 3, 4, 5]
        events = log.read_all()
        assert [e.id for e in events] == [1, 2, 3, 4, 5]

    def test_list_after_cursor(self, tmp_path):
        """断线重连从事件 ID 游标增量补发。"""
        log = EventLog(tmp_path / "events.jsonl")
        log.append_many([make_event(i) for i in range(1, 6)])
        after = log.list_after(3)
        assert [e.id for e in after] == [4, 5]

    def test_last_id_tail_pointer(self, tmp_path):
        log = EventLog(tmp_path / "events.jsonl")
        assert log.last_id() == 0
        log.append(make_event(1))
        assert log.last_id() == 1
        log.append_many([make_event(2), make_event(3)])
        assert log.last_id() == 3

    def test_durable_append_only(self, tmp_path):
        """append-only：重读不改变事实源。"""
        log = EventLog(tmp_path / "events.jsonl")
        log.append(make_event(1))
        log.append(make_event(2))
        # 多次读结果一致
        assert len(log.read_all()) == 2
        assert len(log.read_all()) == 2

    def test_corrupt_line_skipped(self, tmp_path):
        """损坏行容错跳过（append-only 容错）。"""
        p = tmp_path / "events.jsonl"
        p.write_text('{"not":"an event"}\n', encoding="utf-8")
        log = EventLog(p)
        log.append(make_event(1))
        events = log.read_all()
        assert len(events) == 1
        assert events[0].id == 1

    def test_tail_watcher_incremental(self, tmp_path):
        """跨进程尾指针监听：增量拉取。"""
        log = EventLog(tmp_path / "events.jsonl")
        log.append_many([make_event(1), make_event(2)])
        watcher = TailWatcher(log, poll_interval=0.01)
        assert watcher._last_seen == 2  # 初始同步
        log.append(make_event(3))
        new = watcher.poll()
        assert [e.id for e in new] == [3]
        assert watcher.poll() == []  # 无新增

    def test_on_event_listener(self, tmp_path):
        log = EventLog(tmp_path / "events.jsonl")
        received = []
        detach = log.on_event(lambda ev: received.append(ev.id))
        log.append(make_event(1))
        assert received == [1]
        detach()
        log.append(make_event(2))
        assert received == [1]  # 注销后不再通知
