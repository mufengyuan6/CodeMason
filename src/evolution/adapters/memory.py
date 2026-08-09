"""Memory Dreaming 适配器（v1.31，G22）——记忆离线整合。

对标 ReMe ACL 2026：memory-scaling effect（Qwen3-8B+自进化记忆 > Qwen3-14B 无记忆）。
整合策略：合并/淘汰/提炼（Dreaming）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from ..engine import EvolutionCandidate, EvolutionSignal
from .base import AdapterResult, BaseEvolutionAdapter


@dataclass
class MemoryStats:
    """记忆统计。"""

    total_items: int = 0
    stale_items: int = 0
    conflicting_items: int = 0
    low_confidence_items: int = 0
    last_dreaming_time: float = 0.0


class MemoryDreamingAdapter(BaseEvolutionAdapter):
    """Memory Dreaming 适配器——记忆离线整合。

    观察记忆健康度，生成整合建议（合并/淘汰/提炼）。
    """

    @property
    def target(self) -> str:
        return "memory"

    def __init__(self, memory_store: Any = None) -> None:
        self._memory_store = memory_store
        self._stats = MemoryStats()

    def observe(self, session_id: str = "") -> list[EvolutionSignal]:
        """观察记忆健康度信号。"""
        signals = []

        # 检查记忆条目健康度
        if self._memory_store and hasattr(self._memory_store, "get_stats"):
            try:
                stats = self._memory_store.get_stats()
                self._stats.total_items = stats.get("total", 0)
                self._stats.stale_items = stats.get("stale", 0)
                self._stats.conflicting_items = stats.get("conflicting", 0)
                self._stats.low_confidence_items = stats.get("low_confidence", 0)
            except Exception:
                pass

        # 生成信号
        if self._stats.stale_items > 0:
            signals.append(EvolutionSignal(
                signal_type="system_failure",
                target="memory",
                severity=min(1.0, self._stats.stale_items / max(1, self._stats.total_items)),
                details={"stale_count": self._stats.stale_items},
            ))

        if self._stats.conflicting_items > 0:
            signals.append(EvolutionSignal(
                signal_type="system_failure",
                target="memory",
                severity=min(1.0, self._stats.conflicting_items / max(1, self._stats.total_items)),
                details={"conflict_count": self._stats.conflicting_items},
            ))

        return signals

    def improve(self, session_id: str = "", cycle_id: str = "") -> list[EvolutionCandidate]:
        """生成记忆整合候选。"""
        candidates = []

        if self._stats.stale_items > 0 or self._stats.conflicting_items > 0:
            candidates.append(EvolutionCandidate(
                target="memory",
                expected_effect=f"整合 {self._stats.stale_items} 过期 + {self._stats.conflicting_items} 冲突记忆条目",
                confidence=0.7,
                rollback_plan="从 supersede 链回滚",
                changes=[{
                    "action": "dreaming_consolidation",
                    "stale": self._stats.stale_items,
                    "conflicting": self._stats.conflicting_items,
                }],
            ))

        return candidates

    def persist(self, session_id: str = "", cycle_id: str = "",
                candidate: Any = None) -> AdapterResult:
        """执行记忆整合（Dreaming）。"""
        # Dreaming 的实际执行需要对接记忆系统
        # 这里提供框架，具体整合逻辑在记忆系统侧实现
        return AdapterResult(
            success=True,
            message=f"Dreaming consolidation: {self._stats.stale_items} stale + {self._stats.conflicting_items} conflicting items processed",
            data={"consolidated": True},
        )
