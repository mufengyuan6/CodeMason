"""Agent 内部事件类型。

内部事件流 = Agent Loop 的状态转换输入（区别于 protocol/events.py 的对外 Event）。
四类终止条件：完成 / 需澄清 / 超步数 / 异常。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class InternalEventType(str, Enum):
    """Agent Loop 内部事件类型。"""

    USER_MESSAGE = "user_message"
    AGENT_REASON = "agent_reason"
    PLAN = "plan"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ASK_USER = "ask_user"
    FINISH = "finish"
    ERROR = "error"


class TerminationReason(str, Enum):
    """四类终止条件。"""

    COMPLETED = "completed"          # 完成
    NEEDS_CLARIFICATION = "needs_clarification"  # 需澄清
    MAX_ITERATIONS = "max_iterations"  # 超步数
    EXCEPTION = "exception"          # 异常


class InternalEvent(BaseModel):
    """Agent Loop 内部事件（不可变，范式声明：事件模型 frozen）。"""

    model_config = ConfigDict(frozen=True)

    type: InternalEventType
    content: Any = Field(default=None)
    turn_index: int = Field(default=0)
    step_index: int = Field(default=0)
    meta: dict = Field(default_factory=dict)
