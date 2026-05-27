"""项目层记忆：行为契约 + 项目事实表 + 活状态投影（G12 / 3.1）。

v1.13 补强（对照差异表 🟡 Missing）：
- **PROJECT_FACTS.md 事实表**：agent 任务完成时追加可验证的结构化事实，带 attributed_to +
  溯源事件 ID；分级确认（可验证级自动生效 / 语义级走用户纠正确认门）
- **活状态文件**（对标 MemClaw living README，去外部化）：从事件流投影项目当前状态
  （pod 进度/改动文件/待办），SessionStart 注入
- **source_trust + pinned**：user_confirmed 级事实标记 pinned=true，压缩/遗忘永不丢弃
- AGENTS.md/.clinerules 行为契约加载（已有，保留）
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional


class ProjectMemory:
    """项目层记忆：读取项目规则文件（AGENTS.md/.clinerules）+ 项目事实表 + 活状态。"""

    RULE_FILES = ["AGENTS.md", "CLAUDE.md", ".clinerules"]
    FACTS_FILE = "PROJECT_FACTS.md"
    STATE_FILE = ".codemason_state.json"

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root)
        self._rules: list[dict] = []
        self._bug_patterns: list[dict] = []
        self._facts: list[dict] = []  # PROJECT_FACTS 投影
        self._load_rules()
        self._load_facts()

    def _load_rules(self) -> None:
        for fname in self.RULE_FILES:
            p = self.project_root / fname
            if p.exists():
                self._rules.append({"file": fname, "content": p.read_text(encoding="utf-8", errors="replace")[:8000]})

    def _facts_path(self) -> Path:
        return self.project_root / self.FACTS_FILE

    def _load_facts(self) -> None:
        p = self._facts_path()
        if not p.exists():
            return
        content = p.read_text(encoding="utf-8", errors="replace")
        for line in content.splitlines():
            m = re.match(r"- \[(confirmed|agent_inferred|user_corrected)\]\s+(.+)$", line.strip())
            if m:
                self._facts.append(
                    {"trust": m.group(1), "fact": m.group(2), "pinned": m.group(1) == "confirmed", "ts": time.time()}
                )

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

    # ---------- PROJECT_FACTS（v1.13 新增） ----------

    def add_fact(
        self,
        fact: str,
        *,
        trust: str = "agent_inferred",
        attributed_to: str = "assistant",
        provenance_event_id: Optional[int] = None,
    ) -> dict:
        """追加可验证的结构化事实。

        分级确认：可验证级（构建命令/测试框架等可从命令历史确定性确认）可直接 confirmed；
        语义级（"这是核心模块"）默认 agent_inferred，走用户纠正确认门升级。
        """
        entry = {
            "fact": fact,
            "trust": trust,
            "pinned": trust in ("confirmed", "user_corrected"),
            "attributed_to": attributed_to,
            "provenance_event_id": provenance_event_id,
            "ts": time.time(),
        }
        self._facts.append(entry)
        self._sync_facts_file()
        return entry

    def confirm_fact(self, fact_text: str) -> Optional[dict]:
        """用户纠正确认门：语义级事实经用户确认升级为 confirmed（pinned）。"""
        for entry in self._facts:
            if entry["fact"] == fact_text and entry["trust"] != "confirmed":
                entry["trust"] = "confirmed"
                entry["pinned"] = True
                self._sync_facts_file()
                return entry
        return None

    def get_pinned_facts(self) -> list[dict]:
        """pinned facts（压缩/遗忘豁免类别——"用户确认过的事实"永不丢弃）。"""
        return [f for f in self._facts if f.get("pinned")]

    def _sync_facts_file(self) -> None:
        lines = ["# PROJECT_FACTS（CodeMason 项目事实表）", "", "> 格式：- [trust] 事实描述", "> confirmed=用户确认（pinned 永不压缩）/ user_corrected=用户纠正过 / agent_inferred=模型推断未确认", ""]
        for f in self._facts:
            lines.append(f"- [{f['trust']}] {f['fact']}")
        self._facts_path().write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------- 活状态投影（v1.13 新增，对标 MemClaw living README） ----------

    def update_state(self, **fields: Any) -> dict:
        """投影项目当前状态（pod 进度/改动文件/待办），SessionStart 注入。"""
        state = self.get_state()
        state.update(fields)
        state["updated_at"] = time.time()
        (self.project_root / self.STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return state

    def get_state(self) -> dict:
        p = self.project_root / self.STATE_FILE
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"pod": "idle", "modified_files": [], "todo": []}

    def to_dict(self) -> dict:
        return {"rules": self._rules, "bug_patterns": self._bug_patterns, "facts": self._facts, "state": self.get_state()}


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
