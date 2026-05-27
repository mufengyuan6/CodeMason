"""工具 schema 动态裁剪（3.2 阶段2，对标 mcp-cli 动态工具定义，P0）。

- Op/Event 协议天然记录每轮工具调用——最近 N 轮未使用的工具定义从发送给模型的 schema 中摘除
  （Tool/MCP 定义 30K 是固定前缀 Top2 成本），使用时再恢复
- 数据驱动自适应裁剪（协议即使用台账，越用越省）
- 工具注册表保留全量，只裁剪发送内容（不丢注册）
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SchemaPruneStats:
    """裁剪统计：省了多少 token。"""

    total_removed: int = 0        # 累计摘除的工具次数
    last_removed: list[str] = field(default_factory=list)
    restored: int = 0             # 恢复次数

    def to_dict(self) -> dict:
        return {"total_removed": self.total_removed, "last_removed": self.last_removed, "restored": self.restored}


class ToolSchemaPruner:
    """工具 schema 动态裁剪器。

    用法：
        pruner = ToolSchemaPruner(all_tools=tools, recent_window=6)
        # 每轮组装前：
        schema = pruner.prune(current_turn_calls)
        # 工具被调用时：
        pruner.observe_call(name)
    """

    def __init__(self, all_tools: list[dict], recent_window: int = 6, min_keep: int = 4) -> None:
        self.all_tools = all_tools
        self.recent_window = recent_window
        self.min_keep = min_keep  # 至少保留的工具数（防裁剪过头）
        self._recent_calls: deque[str] = deque(maxlen=recent_window)
        self._force_keep: set[str] = set()  # 显式保留（如 AskUser/Monitor 常驻）
        self.stats = SchemaPruneStats()

    def force_keep(self, *names: str) -> None:
        """显式保留工具（常驻工具不裁剪）。"""
        self._force_keep.update(names)

    def observe_call(self, name: str) -> None:
        """记录一次工具调用（Op/Event 台账驱动——协议即使用台账）。"""
        self._recent_calls.append(name)

    def prune(self) -> list[dict]:
        """返回发送给模型的 schema（裁剪后）。最近 N 轮未使用的工具摘除。"""
        used = set(self._recent_calls)
        # 决策：哪些摘除（不在最近 N 轮、不在 force_keep、且数量允许）
        removable = [
            t for t in self.all_tools
            if t.get("name") not in used and t.get("name") not in self._force_keep
        ]
        keep_count = len(self.all_tools) - len(removable)
        if keep_count < self.min_keep:
            # 裁剪过头保护：最多裁到剩 min_keep 个
            removable = removable[: len(removable) - (self.min_keep - keep_count)]
        removed_names = [t.get("name") for t in removable]
        if removed_names:
            self.stats.total_removed += len(removed_names)
            self.stats.last_removed = removed_names
        keep = [t for t in self.all_tools if t.get("name") not in removed_names]
        return keep

    def restore(self, name: str) -> list[dict]:
        """工具被调用时恢复其定义（下次组装自动包含——used 集合命中）。"""
        self.observe_call(name)
        self.stats.restored += 1
        return self.prune()

    def snapshot(self) -> dict:
        """当前裁剪状态（成本驾驶舱展示）。"""
        return {
            "total_tools": len(self.all_tools),
            "sent": len(self.prune()),
            "removed": len(self.all_tools) - len(self.prune()),
            "stats": self.stats.to_dict(),
        }
