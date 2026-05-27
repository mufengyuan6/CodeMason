"""会话层记忆：事件流 + sidecar 摘要视图组装（G12）。

核心纪律（v1.13 / P0）：
- **compact 不重写事件文件**：原始 JSONL 永远 append-only，压缩产物是 sidecar 摘要视图
  （.summary.json），LLM 上下文从"事件流 + 摘要视图"组装
- **摘要水印（对标 graphiti SagaNode）**：sidecar 记录 first/last_event_id 覆盖范围，
  增量摘要只重算新事件范围——摘要成本随会话线性而非平方增长
- 记忆是 EventLog 的确定性投影（读模型），可随时重放重建，不制造第二事实源
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional


class SessionMemory:
    """会话记忆：JSONL 持久化 + 消息追加 + sidecar 摘要压缩（不重写事件文件）。"""

    def __init__(self, path: str | Path, max_messages: int = 100) -> None:
        self.path = Path(path)
        self.max_messages = max_messages
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._messages: list[dict] = []
        self._summary: Optional[dict] = None  # sidecar 摘要视图
        self._load()

    def _summary_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".summary.json")

    def _load(self) -> None:
        # 1. 读 sidecar 摘要（如有）——压缩产物，不重写事件文件
        sp = self._summary_path()
        if sp.exists():
            try:
                self._summary = json.loads(sp.read_text(encoding="utf-8"))
            except Exception:
                self._summary = None
        # 2. 读事件文件（append-only 事实源）
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
        msg = {
            "role": role,
            "content": content,
            "ts": time.time(),
            "meta": meta or {},
            "event_id": (self._messages[-1].get("event_id", 0) + 1) if self._messages else 1,
        }
        self._messages.append(msg)
        # 事件文件 append-only：永不重写
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(msg, ensure_ascii=False) + "\n")
        return msg

    def get_messages(self) -> list[dict]:
        return list(self._messages)

    def get_context(self, limit: Optional[int] = None) -> list[dict]:
        """取上下文（默认最近 N 条，供 LLM 调用）——从"摘要视图 + 事件流"组装。"""
        n = limit or self.max_messages
        if self._summary is not None:
            # sidecar 摘要视图：摘要条目 + 最近 N 条原始事件
            head = [{"role": "system", "content": f"[会话摘要 {self._summary.get('summary', '')}]", "ts": self._summary.get("ts", 0), "meta": {"sidecar": True, "event_range": [self._summary.get("first_event_id"), self._summary.get("last_event_id")]}}]
            return head + self._messages[-n:]
        return self._messages[-n:]

    def needs_compact(self, threshold: Optional[int] = None) -> bool:
        return len(self._messages) > (threshold or self.max_messages)

    def compact(self, summary: str) -> dict:
        """auto-compact：**不重写事件文件**——只生成 sidecar 摘要视图（带水印）。

        原始 JSONL 保持 append-only（事件溯源唯一真相），摘要视图覆盖 [first, last] 范围，
        后续增量压缩只重算新事件范围。
        """
        first_event_id = self._messages[0].get("event_id", 1) if self._messages else 1
        last_event_id = self._messages[-1].get("event_id", first_event_id) if self._messages else first_event_id
        keep = self._messages[-self.max_messages // 2:]
        self._summary = {
            "summary": summary,
            "first_event_id": first_event_id,
            "last_event_id": last_event_id,
            "ts": time.time(),
            "coverage_count": len(self._messages),
        }
        # sidecar 写入（覆盖更新——它是派生视图，可重算）
        self._summary_path().write_text(json.dumps(self._summary, ensure_ascii=False), encoding="utf-8")
        self._messages = keep
        return {"messages": len(self._messages), "summary": summary, "first_event_id": first_event_id, "last_event_id": last_event_id}

    def event_file_intact(self) -> bool:
        """事件文件完整性校验：压缩后事件文件仍包含全部原始消息（compact 不重写）。"""
        if not self.path.exists():
            return False
        count = sum(1 for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip())
        return count >= (self._summary.get("coverage_count", 0) if self._summary else count)

    def stats(self) -> dict:
        return {
            "messages": len(self._messages),
            "size": self.path.stat().st_size if self.path.exists() else 0,
            "summary": self._summary is not None,
        }
