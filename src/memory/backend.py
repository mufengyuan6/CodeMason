"""MemoryBackend 薄接口（G12 存储抽象，企业化前提）。

记忆层定义 append / read_after(cursor) / project 作用域三方法，当前实现本地 JSONL——
企业化时换 SQLite/Postgres/对象存储投影语义不变（CQRS 读模型与存储解耦；预留多 agent 共享记忆）。
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterator, Optional


class MemoryBackend(ABC):
    """记忆存储抽象：三种操作语义不变，换存储不改记忆层。"""

    @abstractmethod
    def append(self, entry: dict, project_scope: str = "global") -> str:
        """追加一条记忆记录，返回记录 ID。"""

    @abstractmethod
    def read_after(self, cursor: str = "", project_scope: str = "global", limit: int = 100) -> list[dict]:
        """游标增量读取（断点恢复/增量投影）。"""

    @abstractmethod
    def read_all(self, project_scope: Optional[str] = None) -> list[dict]:
        """全量读取（可过滤 project 作用域）。"""


class JsonlMemoryBackend(MemoryBackend):
    """本地 JSONL 实现：append-only，符合事件溯源哲学。

    文件格式：每行一个记忆记录 JSON。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    rec_id = rec.get("id", "")
                    if rec_id.startswith("mem-"):
                        n = int(rec_id[4:])
                        if n > self._seq:
                            self._seq = n
                except Exception:
                    continue

    def _next_id(self) -> str:
        self._seq += 1
        return f"mem-{self._seq}"

    def append(self, entry: dict, project_scope: str = "global") -> str:
        rec = dict(entry)
        rec.setdefault("id", self._next_id())
        rec["project_scope"] = project_scope
        rec.setdefault("created_at", time.time())
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec["id"]

    def read_after(self, cursor: str = "", project_scope: str = "global", limit: int = 100) -> list[dict]:
        out = []
        seen_cursor = False
        for line in self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if cursor and rec.get("id") != cursor and not seen_cursor:
                continue
            if rec.get("id") == cursor:
                seen_cursor = True
                continue
            if seen_cursor or not cursor:
                if project_scope and rec.get("project_scope") not in (project_scope, "global"):
                    continue
                out.append(rec)
                if len(out) >= limit:
                    break
        return out

    def read_all(self, project_scope: Optional[str] = None) -> list[dict]:
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if project_scope and rec.get("project_scope") not in (project_scope, "global"):
                continue
            out.append(rec)
        return out
