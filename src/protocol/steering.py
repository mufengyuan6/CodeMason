"""Steering 消息分级（G16⑤ v1.23 落地：架构开放性——Steering 消息分级）。

设计（design.md G16⑤）：
- 用户在工作流中发的新消息分三类：排队消息（下一轮任务）/ 注入上下文（补充当前轮）/
  Steering 转向指令（立即改变方向）
- 回执确认（SteeringAck 事件）："模型在哪一步看到了它"——不只"消息收到了"，
  还关心"模型究竟在哪一步看到"
- 长任务中"用户打断 vs 追加任务"语义区分（驾驶舱运行中发消息的确定性语义）

范式声明：协议层 = 枚举 + pydantic。
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = "v1"


class SteeringCategory(str, Enum):
    """Steering 消息分类（G16⑤：排队/注入/转向）。"""

    QUEUED = "queued"            # 排队消息：下一轮任务（不打断当前轮）
    INJECT = "inject"            # 注入上下文：补充当前轮（进入模型上下文）
    STEERING = "steering"        # 转向指令：立即改变方向（中断当前计划）


class SteeringMessage(BaseModel):
    """一条 Steering 消息（用户在工作流中发的新消息）。"""

    model_config = ConfigDict(frozen=True)

    protocol_version: str = Field(default=PROTOCOL_VERSION)
    message_id: str = Field(default_factory=lambda: f"steer-{uuid4().hex[:8]}", description="消息 id（幂等）")
    category: SteeringCategory = Field(description="消息分类（排队/注入/转向）")
    content: str = Field(description="消息内容")
    ts: float = Field(default=0.0, description="发送时间戳")
    session_id: str = Field(default="", description="会话 id")


class SteeringAck(BaseModel):
    """Steering 回执（SteeringAck 事件数据源）。

    回执确认"模型在哪一步看到了它"——可审计（企业长任务语义确定性的基础）。
    """

    model_config = ConfigDict(frozen=True)

    protocol_version: str = Field(default=PROTOCOL_VERSION)
    message_id: str = Field(description="对应 SteeringMessage 的 id")
    category: SteeringCategory = Field(description="消息分类")
    seen_at: str = Field(default="queued", description="模型看到它的位置（queued/next_turn/current_prompt）")
    processed: bool = Field(default=False, description="是否已消费")
    note: str = Field(default="", description="处理说明")

    def model_dump_compact(self) -> dict:
        return {
            "message_id": self.message_id,
            "category": self.category.value,
            "seen_at": self.seen_at,
            "processed": self.processed,
        }
