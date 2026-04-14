"""Op 协议：界面/外部 → 内核的意图（对标 Codex SQ/EQ 的 Op Queue）。

职责边界写死（G3）：
- Op = 用户/外部对内核的**意图**（UserTurnStart / ApprovalResponse / UserTurnCancel / Compact）
- 不为每个 UI 按钮发明新 Op：Plan/Act 切换是 Compact/UserTurnStart 的变体
- 每个 Op 带幂等 id（重复提交不重复执行）
- schema 版本化：protocol_version 字段，v1/v2 演进兼容
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, Field

# 协议版本：v1 为当前实现，演进只增不改
PROTOCOL_VERSION = "v1"


class OpType(str, Enum):
    """Op 类型枚举（界面→core 的全部意图集合）。"""

    USER_TURN_START = "UserTurnStart"
    USER_TURN_CANCEL = "UserTurnCancel"
    APPROVAL_RESPONSE = "ApprovalResponse"
    COMPACT = "Compact"


class Op(BaseModel):
    """Op 基类：所有 Op 共享协议版本 + 幂等 id。"""

    protocol_version: str = Field(default=PROTOCOL_VERSION, description="协议版本")
    op_id: str = Field(default_factory=lambda: uuid4().hex, description="幂等 id，重复提交不重复执行")
    type: OpType


class UserTurnStart(Op):
    """用户发起一轮新对话（Agent 忙时入队，跑完自动处理——对标 pi-web prompt queuing）。

    Plan/Act 切换 = mode 字段变体，不发明新 Op。
    """

    type: Literal[OpType.USER_TURN_START] = OpType.USER_TURN_START
    content: str = Field(description="用户消息内容")
    mode: Literal["act", "plan"] = Field(default="act", description="会话模式：act 执行 / plan 规划")
    files: list[str] = Field(default_factory=list, description="@file / @folder 显式引用")
    session_id: Optional[str] = Field(default=None, description="目标会话 id，None 表示新建")


class UserTurnCancel(Op):
    """用户取消当前轮次。"""

    type: Literal[OpType.USER_TURN_CANCEL] = OpType.USER_TURN_CANCEL
    reason: Optional[str] = Field(default=None, description="取消原因")


class ApprovalResponse(Op):
    """用户对 ExecApprovalRequest 的响应（批准/拒绝/修改后批准）。

    幂等：同一 approval_id 重复提交只生效一次。
    """

    type: Literal[OpType.APPROVAL_RESPONSE] = OpType.APPROVAL_RESPONSE
    approval_id: str = Field(description="对应 ExecApprovalRequest 的事件 id")
    decision: Literal["approve", "reject", "edit"] = Field(description="approve 批准 / reject 拒绝 / edit 修改")
    edited_command: Optional[str] = Field(default=None, description="decision=edit 时提供修改后的命令")


class Compact(Op):
    """请求上下文压缩（超窗口阈值时也可自动触发）。"""

    type: Literal[OpType.COMPACT] = OpType.COMPACT
    target: Literal["context", "session"] = Field(default="context", description="压缩目标")


OpUnion = Annotated[
    Union[UserTurnStart, UserTurnCancel, ApprovalResponse, Compact],
    Field(discriminator="type"),
]
