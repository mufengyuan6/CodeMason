"""Event 协议：内核 → 界面/外部的实事（对标 Codex SQ/EQ 的 Event Queue）。

职责边界写死（G3）：
- Event = 内核对外的**事实**（TurnStarted / AgentMessageContentDelta / ExecApprovalRequest / ItemCompleted）
- 所有 Event 带全局递增 id（JSONL 游标增量补发的基础）+ schema 版本
- 判别联合 + frozen=True（范式声明：事件模型层 OOP pydantic）

架构要点（G3 / 1.4）：
- Event id 单调递增 → WebSocket 断线重连后从游标增量补发
- 不直接面向 UI 发指令：UI 从 Event 渲染，状态一律由事件流推导
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

# 协议版本：与 Op 同步
PROTOCOL_VERSION = "v1"


class EventType(str, Enum):
    """Event 类型枚举（core→界面的事实集合）。"""

    TURN_STARTED = "TurnStarted"
    AGENT_MESSAGE_DELTA = "AgentMessageContentDelta"
    EXEC_APPROVAL_REQUEST = "ExecApprovalRequest"
    ITEM_COMPLETED = "ItemCompleted"
    TURN_CANCELLED = "TurnCancelled"
    ERROR = "Error"
    ROLLBACK = "Rollback"


class Event(BaseModel):
    """Event 基类：全局递增 id + 协议版本。"""

    model_config = ConfigDict(frozen=True)

    id: int = Field(description="全局递增事件 id（游标增量补发基准）")
    protocol_version: str = Field(default=PROTOCOL_VERSION, description="协议版本")
    type: EventType
    ts: float = Field(description="事件时间戳（epoch 秒）")


class TurnStarted(Event):
    """一轮新对话开始（对应 Op: UserTurnStart 被接受）。"""

    type: Literal[EventType.TURN_STARTED] = EventType.TURN_STARTED
    session_id: str
    mode: Literal["act", "plan"]
    turn_index: int
    op_id: str = Field(description="触发本轮的 UserTurnStart 的 op_id（幂等追踪）")


class AgentMessageContentDelta(Event):
    """Agent 消息增量（流式输出，UI 按 deltas 拼接渲染）。"""

    type: Literal[EventType.AGENT_MESSAGE_DELTA] = EventType.AGENT_MESSAGE_DELTA
    session_id: str
    message_index: int
    delta: str
    complete: bool = Field(default=False, description="本消息是否结束（delta 为空且 complete=True 表示收尾）")


class ExecApprovalRequest(Event):
    """工具执行需要审批（危险操作进入 WAITING_FOR_CONFIRMATION，事件留库不执行）。"""

    type: Literal[EventType.EXEC_APPROVAL_REQUEST] = EventType.EXEC_APPROVAL_REQUEST
    session_id: str
    approval_id: str = Field(description="审批 id（ApprovalResponse 引用）")
    tool_name: str
    description: str = Field(description="命令/操作的人类可读描述")
    command: str = Field(default="", description="待执行的 shell 命令（Bash 工具）")
    risk_level: Literal["red", "yellow", "green"] = Field(default="red", description="风险等级（红/黄/绿）")
    diff_preview: Optional[str] = Field(default=None, description="写入类操作的 diff 预览（审批中心展示）")


class ItemCompleted(Event):
    """一个工作项完成（工具调用结果 / 子任务结论 / 回合总结）。"""

    type: Literal[EventType.ITEM_COMPLETED] = EventType.ITEM_COMPLETED
    session_id: str
    item_type: Literal["tool_result", "subagent_result", "turn_summary", "task_result"]
    item_id: str
    content: Optional[Any] = Field(default=None, description="结构化结果")
    metrics: Optional[dict] = Field(default=None, description="YAGNI 四维量化指标（可选）")


class TurnCancelled(Event):
    """用户取消了当前轮次（对应 Op: UserTurnCancel）。"""

    type: Literal[EventType.TURN_CANCELLED] = EventType.TURN_CANCELLED
    session_id: str
    reason: Optional[str] = None


class Error(Event):
    """内核错误（不中断会话，仅上报）。"""

    type: Literal[EventType.ERROR] = EventType.ERROR
    session_id: str
    message: str
    error_type: str = Field(default="unknown", description="错误分类（语法/权限/路径/逻辑/网络）")


class Rollback(Event):
    """事件溯源回滚（G4）：追加 rollback 事件 + 代码状态复位。"""

    type: Literal[EventType.ROLLBACK] = EventType.ROLLBACK
    session_id: str
    checkpoint_id: str
    reason: str = Field(default="user_requested", description="回滚原因")
    truncated_event_id: Optional[int] = Field(default=None, description="事件流截断标记")


EventUnion = Annotated[
    Union[
        TurnStarted,
        AgentMessageContentDelta,
        ExecApprovalRequest,
        ItemCompleted,
        TurnCancelled,
        Error,
        Rollback,
    ],
    Field(discriminator="type"),
]
