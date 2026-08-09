"""Harness 在线化适配器（v1.31，G22）——Harness prompt/schema 在线化改进。

对标 harness-training（workofart）：git-as-optimizer，评测驱动离线改进。
"""

from __future__ import annotations

from typing import Any

from ..engine import EvolutionCandidate, EvolutionSignal
from .base import AdapterResult, BaseEvolutionAdapter


class HarnessOnlineAdapter(BaseEvolutionAdapter):
    """Harness 在线化适配器。"""

    @property
    def target(self) -> str:
        return "harness"

    def observe(self, session_id: str = "") -> list[EvolutionSignal]:
        return []

    def improve(self, session_id: str = "", cycle_id: str = "") -> list[EvolutionCandidate]:
        return []

    def persist(self, session_id: str = "", cycle_id: str = "",
                candidate: Any = None) -> AdapterResult:
        return AdapterResult(success=True, message="no-op")
