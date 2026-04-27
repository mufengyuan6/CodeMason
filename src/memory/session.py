"""会话层记忆。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional


class SessionMemory:
    """会话记忆：JSONL 持久化 + 消息追加 + 摘要压缩。"""

    def __init__(self, path: str | Path, max_messages: int = 100) -> None:
        self.path = Path(path)
        self.max_messages = max_messages
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._messages: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    self._messages.append(json.loads(line))
                except Exception:
                    continue

    def append(self, role: str, content: str, meta: Optional[dict] = None) -> dict:
        msg = {"role": role, "content": content, "ts": time.time(), "meta": meta or {}}
        self._messages.append(msg)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(msg, ensure_ascii=False) + "\n")
        return msg

    def get_messages(self) -> list[dict]:
        return list(self._messages)

    def get_context(self, limit: Optional[int] = None) -> list[dict]:
        """取上下文（默认最近 N 条，供 LLM 调用）。"""
        n = limit or self.max_messages
        return self._messages[-n:]

    def needs_compact(self, threshold: Optional[int] = None) -> bool:
        return len(self._messages) > (threshold or self.max_messages)

    def compact(self, summary: str) -> dict:
        """auto-compact：保留摘要 + 最近 N 条。"""
        keep = self._messages[-self.max_messages // 2:]
        self._messages = [{"role": "system", "content": f"[会话摘要] {summary}", "ts": time.time()}] + keep
        self._rewrite()
        return {"messages": len(self._messages), "summary": summary}

    def _rewrite(self) -> None:
        with self.path.open("w", encoding="utf-8") as fh:
            for msg in self._messages:
                fh.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def stats(self) -> dict:
        return {"messages": len(self._messages), "size": self.path.stat().st_size if self.path.exists() else 0}
