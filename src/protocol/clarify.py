"""澄清策略（G13 v1.23 落地：多轮对话编排——澄清策略 Ask or Assume）。

设计（design.md G13）：
- 不确定性感知（每轮显式 Ambiguity assessment，有歧义才问，防过度提问——
  Kimi K2.6 把绝大多数任务误标 underspecified 导致完成率下降，arXiv 2603.26233）
- 结构化提问（Codex request_user_input：只问会实质改变计划的问题、总带推荐选项）
- ClarificationRequested 事件进事件流

范式声明：协议层 = 枚举 + pydantic。
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = "v1"


class AmbiguityLevel(str, Enum):
    """歧义等级（不确定性感知）。"""

    NONE = "none"              # 无歧义（不问）
    LOW = "low"                # 低歧义（可自行假设）
    MEDIUM = "medium"          # 中歧义（有推荐选项时问）
    HIGH = "high"              # 高歧义（必须澄清）


class ClarificationOption(BaseModel):
    """一个澄清选项（结构化提问总带推荐选项）。"""

    model_config = ConfigDict(frozen=True)

    label: str = Field(description="选项标签")
    description: str = Field(default="", description="选项说明")


class ClarificationRequested(BaseModel):
    """澄清请求（ClarificationRequested 事件数据源）。

    设计约束（Codex request_user_input）：
    - 只问会实质改变计划的问题（Ask or Assume？）
    - 总带推荐选项（redundant = 用户可直接点选）
    """

    model_config = ConfigDict(frozen=True)

    protocol_version: str = Field(default=PROTOCOL_VERSION)
    clarification_id: str = Field(description="澄清 id（幂等追踪）")
    question: str = Field(description="要澄清的问题")
    ambiguity_level: AmbiguityLevel = Field(default=AmbiguityLevel.HIGH)
    options: list[ClarificationOption] = Field(default_factory=list, description="推荐选项")
    context: dict = Field(default_factory=dict, description="触发澄清的上下文（用户消息摘要）")

    def model_dump_compact(self) -> dict:
        return {
            "clarification_id": self.clarification_id,
            "question": self.question,
            "ambiguity_level": self.ambiguity_level.value,
            "option_count": len(self.options),
        }


def should_clarify(level: AmbiguityLevel, *, has_options: bool = True) -> bool:
    """按歧义等级决定是否澄清（不确定性感知策略）。

    - NONE/LOW：不问（可自行假设——防过度提问，Kimi K2.6 教训）
    - MEDIUM：有推荐选项才问（默认可假设）
    - HIGH：必须问
    """
    if level in (AmbiguityLevel.NONE, AmbiguityLevel.LOW):
        return False
    if level == AmbiguityLevel.MEDIUM:
        return has_options
    return True
