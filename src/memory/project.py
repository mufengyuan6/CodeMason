"""项目层记忆。"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional


class ProjectMemory:
    """项目层记忆：读取项目规则文件（AGENTS.md/.clinerules）+ 记录项目 Bug 模式。"""

    RULE_FILES = ["AGENTS.md", "CLAUDE.md", ".clinerules"]

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root)
        self._rules: list[dict] = []
        self._bug_patterns: list[dict] = []
        self._load_rules()

    def _load_rules(self) -> None:
        for fname in self.RULE_FILES:
            p = self.project_root / fname
            if p.exists():
                self._rules.append({"file": fname, "content": p.read_text(encoding="utf-8", errors="replace")[:8000]})

    def get_rules(self) -> list[dict]:
        return list(self._rules)

    def get_rules_text(self, limit: int = 4000) -> str:
        """项目规则拼接文本（注入 Agent 上下文）。"""
        parts = []
        for r in self._rules:
            parts.append(f"## {r['file']}\n{r['content'][:limit]}")
        return "\n\n".join(parts)

    def record_bug_pattern(self, error_type: str, pattern: str, solution: str) -> dict:
        """记录项目 Bug 模式（错误分类 → 修复方案）。"""
        entry = {"error_type": error_type, "pattern": pattern, "solution": solution, "ts": time.time()}
        self._bug_patterns.append(entry)
        return entry

    def match_bug_pattern(self, error_text: str) -> Optional[dict]:
        """按错误文本匹配已知 Bug 模式。"""
        for entry in self._bug_patterns:
            if re.search(entry["pattern"], error_text, re.IGNORECASE):
                return entry
        return None

    def to_dict(self) -> dict:
        return {"rules": self._rules, "bug_patterns": self._bug_patterns}


class BugPatternStore:
    """项目 Bug 模式持久化（JSON 文件）。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._patterns: list[dict] = []
        if self.path.exists():
            try:
                self._patterns = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._patterns = []

    def add(self, error_type: str, pattern: str, solution: str) -> dict:
        entry = {"error_type": error_type, "pattern": pattern, "solution": solution, "ts": time.time()}
        self._patterns.append(entry)
        self._save()
        return entry

    def find(self, error_text: str) -> Optional[dict]:
        for entry in self._patterns:
            if re.search(entry["pattern"], error_text, re.IGNORECASE):
                return entry
        return None

    def all(self) -> list[dict]:
        return list(self._patterns)

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._patterns, ensure_ascii=False, indent=2), encoding="utf-8")
