"""协议层公共工具：Op/Event 序列化、幂等检查、schema 版本校验。"""

from __future__ import annotations

import json
from typing import Union

from pydantic import TypeAdapter, ValidationError

from .events import (
    AgentMessageContentDelta,
    Error as ErrorEvent,
    Event,
    EventType,
    EventUnion,
    ExecApprovalRequest,
    ItemCompleted,
    Rollback,
    TurnCancelled,
    TurnStarted,
)
from .ops import (
    ApprovalResponse,
    Compact,
    Op,
    OpType,
    OpUnion,
    PROTOCOL_VERSION,
    UserTurnCancel,
    UserTurnStart,
)

__all__ = [
    # 基类与枚举
    "Op",
    "OpUnion",
    "OpType",
    "Event",
    "EventUnion",
    "EventType",
    "PROTOCOL_VERSION",
    # Op 具体类
    "UserTurnStart",
    "UserTurnCancel",
    "ApprovalResponse",
    "Compact",
    # Event 具体类
    "TurnStarted",
    "AgentMessageContentDelta",
    "ExecApprovalRequest",
    "ItemCompleted",
    "TurnCancelled",
    "ErrorEvent",
    "Rollback",
    # 工具函数
    "parse_op",
    "parse_event",
    "op_to_json",
    "event_to_json",
]

# 判别联合 TypeAdapter（pydantic 判别联合反序列化）
_op_adapter = TypeAdapter(OpUnion)
_event_adapter = TypeAdapter(EventUnion)


def parse_op(raw: Union[str, dict]) -> Op:
    """解析 Op（JSON 字符串或 dict），带 schema 版本校验 + 判别联合反序列化。"""
    if isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = raw
    version = data.get("protocol_version", PROTOCOL_VERSION)
    if version not in ("v1",):
        raise ValueError(f"不支持的协议版本: {version}")
    return _op_adapter.validate_python(data)


def parse_event(raw: Union[str, dict]) -> Event:
    """解析 Event（JSON 字符串或 dict），判别联合反序列化。"""
    if isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = raw
    return _event_adapter.validate_python(data)


def op_to_json(op: Op) -> str:
    """Op 序列化为 JSON 行（模型 dump，排除 None 保持行紧凑）。"""
    return op.model_dump_json(exclude_none=True)


def event_to_json(event: Event) -> str:
    """Event 序列化为 JSON 行。"""
    return event.model_dump_json(exclude_none=True)
