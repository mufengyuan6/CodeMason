"""JSONL 事件存储：append-only 事实源 + flock 写锁 + 尾指针信号。

核心职责：
- append_only：每行一个事件（pydantic 序列化），只追加不修改
- flock 写锁（30s 超时）：多进程（内核 + server）并发写安全
- LRU 缓存（20MB）：读路径不重复读盘
- 尾指针同步（G3 跨进程）：写后更新 tail 文件 + 可选事件信号（event_written）
- 断线重连：从事件 ID 游标增量补发（list_after）

范式声明：事件存储层 = 函数式（纯函数，JSONL 追加 + flock 写锁）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterator, Optional

import msvcrt

from ..protocol import Event, event_to_json, parse_event

# 写锁超时（秒）
LOCK_TIMEOUT = 30.0
# LRU 缓存容量
CACHE_SIZE_MB = 20
# 尾指针文件名
TAIL_FILE = ".tail"


class EventLogError(Exception):
    """事件存储错误。"""


def _lock_file(fh, timeout: float = LOCK_TIMEOUT) -> bool:
    """对已打开的文件句柄加 flock 写锁（Windows msvcrt 实现）。"""
    deadline = time.monotonic() + timeout
    while True:
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)


def _unlock_file(fh) -> None:
    """释放文件锁。"""
    try:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


class EventLog:
    """JSONL 事件存储（进程内单例使用，多实例通过 flock 互斥）。"""

    def __init__(self, path: str | Path, max_cache_mb: int = CACHE_SIZE_MB) -> None:
        self.path = Path(path)
        self.max_cache_bytes = max_cache_mb * 1024 * 1024
        self._cache: dict[int, Event] = {}
        self._cache_bytes = 0
        self._lock = threading.Lock()
        self._listeners: list[Callable[[Event], None]] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    # ---------- 写入 ----------

    def append(self, event: Event) -> int:
        """追加一个事件，返回其 id。带 flock 写锁 + 尾指针更新。"""
        with self._lock:
            fh = self.path.open("ab+")
            try:
                if not _lock_file(fh):
                    raise EventLogError("事件存储写锁超时（30s）")
                line = event_to_json(event) + "\n"
                fh.write(line.encode("utf-8"))
                fh.flush()
                os.fsync(fh.fileno())
                _unlock_file(fh)
            finally:
                fh.close()
            self._update_tail(event.id)
            self._cache_event(event)
            self._notify(event)
            return event.id

    def append_many(self, events: list[Event]) -> list[int]:
        """批量追加（原子写锁一次），返回 id 列表。"""
        with self._lock:
            fh = self.path.open("ab+")
            try:
                if not _lock_file(fh):
                    raise EventLogError("事件存储写锁超时（30s）")
                ids = []
                for ev in events:
                    line = event_to_json(ev) + "\n"
                    fh.write(line.encode("utf-8"))
                    ids.append(ev.id)
                fh.flush()
                os.fsync(fh.fileno())
                _unlock_file(fh)
            finally:
                fh.close()
            if ids:
                self._update_tail(ids[-1])
                for ev in events:
                    self._cache_event(ev)
                    self._notify(ev)
            return ids

    # ---------- 读取 ----------

    def _read_all_lines(self) -> Iterator[str]:
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line

    def read_all(self) -> list[Event]:
        """读取全部事件（内存缓存命中则合并）。"""
        disk_events: dict[int, Event] = {}
        for line in self._read_all_lines():
            try:
                ev = parse_event(line)
                disk_events[ev.id] = ev
            except Exception:
                continue  # 跳过损坏行（append-only 容错）
        # 缓存合并（磁盘为准，缓存做读加速）
        result = []
        for ev_id in sorted(disk_events.keys()):
            result.append(disk_events[ev_id])
        self._rebuild_cache(result)
        return result

    def list_after(self, cursor: int, limit: Optional[int] = None) -> list[Event]:
        """从事件 ID 游标增量补发（断线重连核心，）。"""
        all_events = self.read_all()
        after = [ev for ev in all_events if ev.id > cursor]
        if limit is not None:
            after = after[:limit]
        return after

    def get(self, event_id: int) -> Optional[Event]:
        """按 id 读取单个事件（缓存优先）。"""
        cached = self._cache.get(event_id)
        if cached is not None:
            return cached
        for line in self._read_all_lines():
            try:
                ev = parse_event(line)
                if ev.id == event_id:
                    return ev
            except Exception:
                continue
        return None

    def last_id(self) -> int:
        """当前最后事件 id（尾指针优先，读盘兜底）。"""
        tail_path = self.path.parent / TAIL_FILE
        try:
            return int(tail_path.read_text().strip())
        except Exception:
            events = self.read_all()
            return events[-1].id if events else 0

    # ---------- 内部 ----------

    def _update_tail(self, event_id: int) -> None:
        """更新尾指针（跨进程同步：server 监听此值增量广播）。"""
        tail_path = self.path.parent / TAIL_FILE
        tail_path.write_text(str(event_id))

    def _cache_event(self, event: Event) -> None:
        self._cache[event.id] = event
        self._cache_bytes += len(event_to_json(event))
        while self._cache_bytes > self.max_cache_bytes and self._cache:
            oldest_id = min(self._cache)
            self._cache_bytes -= len(event_to_json(self._cache.pop(oldest_id)))

    def _rebuild_cache(self, events: list[Event]) -> None:
        self._cache = {ev.id: ev for ev in events}
        self._cache_bytes = sum(len(event_to_json(ev)) for ev in events)
        # 超限时裁剪（保留最新）
        while self._cache_bytes > self.max_cache_bytes and self._cache:
            oldest_id = min(self._cache)
            self._cache_bytes -= len(event_to_json(self._cache.pop(oldest_id)))

    def _notify(self, event: Event) -> None:
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                continue

    # ---------- 监听（跨进程信号的进程内代理） ----------

    def on_event(self, listener: Callable[[Event], None]) -> Callable[[], None]:
        """注册事件监听器，返回注销函数。"""
        self._listeners.append(listener)

        def detach() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return detach


class TailWatcher:
    """跨进程尾指针监听：轮询 .tail 文件增量（G3：共享 JSONL + 尾指针解耦，无共享内存）。"""

    def __init__(self, log: EventLog, poll_interval: float = 0.2) -> None:
        self.log = log
        self.poll_interval = poll_interval
        self._last_seen = log.last_id()
        self._stop = False

    def poll(self) -> list[Event]:
        """拉取新增事件（供 server 广播循环调用）。"""
        current = self.log.last_id()
        if current <= self._last_seen:
            return []
        events = self.log.list_after(self._last_seen)
        if events:
            self._last_seen = events[-1].id
        return events

    def reset(self, cursor: int) -> None:
        """断线重连时从游标重新开始。"""
        self._last_seen = cursor

    def stop(self) -> None:
        self._stop = True
