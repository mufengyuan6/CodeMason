"""多意图分解（G13 v1.23 落地：多轮对话编排——多意图理解）。

设计（design.md G13）：
- UserTurnStart 后轻量意图识别（LLM 判断复合意图 → 拆 2-5 个独立子任务 → 并行派发
  Subagents → 聚合回流，对标 OpenClaw 意图识别层 / Query-Decomposition pattern）
- IntentDecompose 事件进事件流（可审计可回放）

范式声明：协议层 = 枚举 + pydantic（Op/Event 双向契约）。
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = "v1"


class IntentType(str, Enum):
    """意图类型枚举。"""

    SINGLE = "single"            # 单意图（不分解）
    COMPOSITE = "composite"      # 复合意图（需分解）
    AMBIGUOUS = "ambiguous"      # 歧义意图（需澄清）


class SubIntent(BaseModel):
    """一个独立子任务。"""

    model_config = ConfigDict(frozen=True)

    subtask_id: str = Field(description="子任务 id（并行派发引用）")
    description: str = Field(description="子任务描述（独立上下文可执行）")
    requires_read: bool = Field(default=True, description="是否只读（可并行）")
    priority: int = Field(default=1, description="优先级（1 最高）")


class IntentDecompose(BaseModel):
    """意图分解结果（IntentDecompose 事件数据源）。

    对应 design G13：多意图理解 → 拆 2-5 个独立子任务 → 并行派发 Subagents → 聚合回流。
    """

    model_config = ConfigDict(frozen=True)

    protocol_version: str = Field(default=PROTOCOL_VERSION)
    intent_id: str = Field(description="意图 id（幂等追踪）")
    intent_type: IntentType = Field(default=IntentType.SINGLE)
    user_message: str = Field(description="原始用户消息")
    subtasks: list[SubIntent] = Field(default_factory=list, description="分解出的子任务（composite 时非空）")
    confidence: float = Field(default=0.0, description="分解置信度（低则需澄清）")
    ambiguity_hints: list[str] = Field(default_factory=list, description="歧义提示（ambiguous 时给出）")

    @property
    def is_composite(self) -> bool:
        return self.intent_type == IntentType.COMPOSITE

    @property
    def subtask_count(self) -> int:
        return len(self.subtasks)

    def model_dump_compact(self) -> dict:
        """紧凑导出（进事件流/审计）。"""
        return {
            "intent_id": self.intent_id,
            "intent_type": self.intent_type.value,
            "subtask_count": self.subtask_count,
            "confidence": self.confidence,
            "ambiguity_hints": self.ambiguity_hints,
        }
