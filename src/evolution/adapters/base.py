"""进化适配器基类（v1.31，G22）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class AdapterResult:
    """适配器返回结果。"""

    success: bool = True
    message: str = ""
    data: dict = None

    def __post_init__(self) -> None:
        if self.data is None:
            self.data = {}


class BaseEvolutionAdapter(ABC):
    """进化适配器基类——五个作用目标共享的接口。

    每个适配器实现闭环的 observe→analyze→improve→verify→persist 五阶段。
    """

    @property
    @abstractmethod
    def target(self) -> str:
        """作用目标名（memory/skill/planning/tool_usage/harness）。"""
        ...

    def observe(self, session_id: str = "") -> list:
        """Observe 阶段：提取进化信号。

        默认返回空列表（子类覆盖）。
        """
        return []

    def analyze(self, session_id: str = "", signals: list = None) -> dict:
        """Analyze 阶段：归因分析。

        默认返回空分析结果。
        """
        return {"target": self.target, "findings": []}

    def improve(self, session_id: str = "", cycle_id: str = "") -> list:
        """Improve 阶段：生成改进建议。

        默认返回空列表。
        """
        return []

    def verify(self, session_id: str = "", cycle_id: str = "",
               candidate: Any = None) -> dict:
        """Verify 阶段：验证改进候选。

        默认返回验证失败。
        """
        return {"result": "fail", "regression_delta": 0.0}

    def persist(self, session_id: str = "", cycle_id: str = "",
                candidate: Any = None) -> AdapterResult:
        """Persist 阶段：写回系统。

        默认返回空操作。
        """
        return AdapterResult(success=True, message="no-op")
