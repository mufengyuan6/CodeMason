"""跨会话层记忆。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


class GlobalMemory:
    """跨会话经验：任务经验按类型沉淀，同类任务第二次自动注入。"""

    def __init__(self, path: str | Path, max_experiences: int = 200) -> None:
        self.path = Path(path)
        self.max_experiences = max_experiences
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._experiences: dict[str, list[dict]] = {}  # task_type -> [experience]
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._experiences = data.get("experiences", {})
        except Exception:
            self._experiences = {}

    def record(self, task_type: str, summary: str, steps_count: int, success: bool = True) -> dict:
        """记录一次任务经验。"""
        exp = {"summary": summary, "steps": steps_count, "success": success, "ts": time.time()}
        self._experiences.setdefault(task_type, []).append(exp)
        # 裁剪：每类保留最近 20 条
        self._experiences[task_type] = self._experiences[task_type][-20:]
        self._save()
        return exp

    def retrieve(self, task_type: str, limit: int = 3) -> list[dict]:
        """同类任务经验检索（第二次自动注入的依据）。"""
        return list(self._experiences.get(task_type, []))[-limit:]

    def stats(self) -> dict:
        return {k: len(v) for k, v in self._experiences.items()}

    def _save(self) -> None:
        data = {"experiences": self._experiences, "updated_at": time.time()}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
