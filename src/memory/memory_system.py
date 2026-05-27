"""记忆系统：EventLog 事件投影读模型（G12，v1.13 终态）。

**记忆不是独立存储，是 EventLog 的确定性投影**（读模型，可随时重放重建）——
会话/审计/回滚三合一，不制造第二事实源（对标 MemStrata / ES Agent Memory）。

v1.13 重写（差异表 ⚔️ Conflict → L1 直上）：
- 旧实现：三层 key-value（L1/L2/L3 + TTL + consolidate）——非投影、无 supersede、覆盖式写入
- 新实现：以事件流（EventLog）为唯一事实源，记忆条目从结构化事件订阅投影生成
- 保留 MemoryManager 兼容 API（remember/recall/get_session_context/consolidate/get_stats）
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .backend import JsonlMemoryBackend, MemoryBackend
from .global_memory import GlobalMemory
from .project import ProjectMemory
from .session import SessionMemory

# 记忆投影的确定性来源：结构化事件订阅类型（PostToolUse/Stop hook 沉淀）
EVENT_KIND_TASK = "task_result"
EVENT_KIND_TOOL = "tool_result"
EVENT_KIND_ERROR = "error"


class MemoryProjector:
    """事件投影器：从事件流（EventLog）确定性投影记忆条目。

    - 记忆捕获 = 结构化事件订阅：error_type/文件/任务类型沉淀为结构化记忆，零 LLM 成本
    - 每条记忆带 provenance（溯源事件 ID）+ attributed_to + 时态 supersede
    - compact 不重写事件文件：记忆只读事件流 + sidecar 摘要，永不修改事实源
    """

    def __init__(self, backend: Optional[MemoryBackend] = None) -> None:
        self.backend = backend or JsonlMemoryBackend(".codemason/memory.jsonl")

    def subscribe_event(self, event: dict, *, project_scope: str = "global") -> Optional[str]:
        """事件订阅：从结构化事件投影记忆（零 LLM、零噪音、完全可审计）。

        事件 shape（由 hook 产生）：
        - {"kind": "task_result", "task_type": "...", "summary": "...", "success": bool, "steps": int}
        - {"kind": "error", "error_type": "...", "file": "...", "message": "..."}

        幂等：同 provenance_event_id + kind 不重复投影（重放重建不产生重复条目）。
        """
        kind = event.get("kind")
        if kind not in (EVENT_KIND_TASK, EVENT_KIND_ERROR):
            return None
        provenance = event.get("event_id")
        # 幂等检查：同源事件已投影过则跳过（重放重建语义）
        if provenance is not None:
            for rec in self.backend.read_all(project_scope=project_scope):
                if rec.get("provenance_event_id") == provenance and rec.get("type") in ("experience", "error_pattern"):
                    return rec.get("id")
        if kind == EVENT_KIND_TASK:
            return self.backend.append(
                {
                    "type": "experience",
                    "task_type": event.get("task_type", "general"),
                    "summary": event.get("summary", ""),
                    "success": event.get("success", False),
                    "steps": event.get("steps", 0),
                    "attributed_to": event.get("attributed_to", "assistant"),
                    "provenance_event_id": event.get("event_id"),
                },
                project_scope=project_scope,
            )
        if kind == EVENT_KIND_ERROR:
            return self.backend.append(
                {
                    "type": "error_pattern",
                    "error_type": event.get("error_type", "unknown"),
                    "file": event.get("file", ""),
                    "message": event.get("message", "")[:500],
                    "attributed_to": event.get("attributed_to", "assistant"),
                    "provenance_event_id": event.get("event_id"),
                },
                project_scope=project_scope,
            )
        return None

    def replay(self, events: list[dict], *, project_scope: str = "global") -> int:
        """重放重建：从事件流重新投影全部记忆（确定性读模型，可随时重建）。"""
        count = 0
        for ev in events:
            if self.subscribe_event(ev, project_scope=project_scope) is not None:
                count += 1
        return count


class MemoryManager:
    """记忆管理器（兼容 API）：事件投影读模型 + 三视图（会话/项目/全局）。

    旧三层 key-value 语义映射：
    - remember(key, value, level) → 按 level 路由到 会话/项目事实/全局经验
    - recall(key, level) → 检索对应视图
    - consolidate() → 记忆健康整理（归档 + 软衰减清理）
    """

    def __init__(self, base_dir: str = ".codemason", project_root: str | Path = ".") -> None:
        from pathlib import Path

        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.session = SessionMemory(self.base_dir / "session.jsonl")
        self.project = ProjectMemory(project_root)
        self.global_memory = GlobalMemory(self.base_dir / "global.json")
        self.projector = MemoryProjector(JsonlMemoryBackend(self.base_dir / "memory.jsonl"))

    def remember(self, key: str, value: Any, level: int = 1) -> Any:
        """记忆存储（兼容 API）。

        level 映射（旧三层 → 事件投影视图）：
        - 1 = 会话视图（append 消息）
        - 2 = 项目事实表（record fact，agent_inferred 待确认）
        - 3 = 全局经验（record，success=True）
        """
        if level == 1:
            text = value if isinstance(value, str) else str(value)
            return self.session.append("memory", text, meta={"key": key})
        if level == 2:
            return self.project.add_fact(str(value), trust="agent_inferred")
        if level == 3:
            if isinstance(value, dict):
                return self.global_memory.record(
                    value.get("task_type", key),
                    value.get("summary", str(value)),
                    int(value.get("steps", 0)),
                    bool(value.get("success", True)),
                    error_type=value.get("error_type", ""),
                )
            return self.global_memory.record(key, str(value), 0, True)
        return None

    def recall(self, key: str, level: int = 1) -> Optional[Any]:
        """记忆检索（兼容 API）：会话最近 / 项目规则 / 全局同类经验。"""
        if level == 1:
            ctx = self.session.get_context(limit=10)
            return [m for m in ctx if m.get("meta", {}).get("key") == key] or None
        if level == 2:
            for fact in self.project.to_dict().get("facts", []):
                if key in fact.get("fact", ""):
                    return fact
            return None
        if level == 3:
            exps = self.global_memory.retrieve(key, limit=3)
            return exps or None
        return None

    def get_session_context(self, session_id: str) -> dict:
        """获取会话上下文（三视图聚合，SessionStart 注入用）。"""
        return {
            "session_id": session_id,
            "recent_messages": self.session.get_context(limit=5),
            "rules": self.project.get_rules_text(),
            "facts": self.project.to_dict().get("facts", [])[-5:],
            "pinned_facts": self.project.get_pinned_facts(),
            "state": self.project.get_state(),
            "experiences": self.global_memory.stats(),
        }

    def consolidate(self) -> dict:
        """记忆整理：归档超期经验 + 投影器 flush（事件投影读模型无重写）。"""
        archived = self.global_memory.archive_stale()
        return {"archived": archived, "session_events": len(self.session.get_messages())}

    def get_stats(self) -> dict:
        """记忆统计。"""
        return {
            "session_messages": len(self.session.get_messages()),
            "facts": len(self.project.to_dict().get("facts", [])),
            "experiences": self.global_memory.stats(),
            "session_summary": self.session.stats().get("summary", False),
        }
