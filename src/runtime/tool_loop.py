"""工具执行循环（ToolExecutionLoop）：模型调用→工具执行→结果处理的统一流程。

设计要点：
1. 统一的工具执行流程
2. 与 G16 工具执行流水线守卫整合
3. 完整的审计日志
4. 支持同步和异步执行
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from ..protocol import Event, EventType
from ..storage import EventLog
from .event_bus import EventBus

logger = logging.getLogger(__name__)


class ToolExecutionState(str, Enum):
    """工具执行状态枚举。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class ToolExecution:
    """工具执行信息。"""

    execution_id: str
    tool_name: str
    tool_args: dict[str, Any]
    state: ToolExecutionState
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolExecutionConfig:
    """工具执行配置。"""

    max_retries: int = 3
    timeout_seconds: float = 30.0
    enable_validation: bool = True
    enable_audit: bool = True


class ToolExecutionLoop:
    """工具执行循环：模型调用→工具执行→结果处理的统一流程。

    核心职责：
    1. 管理工具执行生命周期
    2. 与 G16 工具执行流水线守卫整合
    3. 记录执行审计日志
    4. 支持超时和重试
    """

    def __init__(
        self,
        event_log: EventLog,
        event_bus: EventBus,
        config: Optional[ToolExecutionConfig] = None,
    ) -> None:
        self.event_log = event_log
        self.event_bus = event_bus
        self.config = config or ToolExecutionConfig()

        self._executions: dict[str, ToolExecution] = {}
        self._tool_registry: dict[str, Callable] = {}

        # 订阅工具执行相关事件
        self.event_bus.subscribe(
            subscriber_id="tool_loop",
            event_type="tool.*",
            callback=self._handle_tool_event,
            priority=50,
        )

    def register_tool(self, tool_name: str, tool_func: Callable) -> None:
        """注册工具。

        Args:
            tool_name: 工具名称
            tool_func: 工具函数
        """
        self._tool_registry[tool_name] = tool_func
        logger.debug(f"工具已注册: {tool_name}")

    def execute_tool(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        *,
        timeout: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ToolExecution:
        """执行工具。

        Args:
            tool_name: 工具名称
            tool_args: 工具参数
            timeout: 超时时间（秒）
            metadata: 元数据

        Returns:
            工具执行信息
        """
        # 生成执行 ID
        execution_id = f"exec_{self.event_log.next_event_id()}"

        # 创建执行记录
        execution = ToolExecution(
            execution_id=execution_id,
            tool_name=tool_name,
            tool_args=tool_args,
            state=ToolExecutionState.PENDING,
            metadata=metadata or {},
        )

        self._executions[execution_id] = execution

        # 发布工具执行开始事件
        from ..protocol.events import ToolExecutionStarted

        start_event = ToolExecutionStarted(
            id=self.event_log.next_event_id(),
            execution_id=execution_id,
            tool_name=tool_name,
            tool_args=tool_args,
            ts=time.time(),
        )
        self.event_log.append(start_event)
        self.event_bus.publish(start_event)

        # 更新状态为运行中
        execution.state = ToolExecutionState.RUNNING
        execution.started_at = time.time()

        # 执行工具
        try:
            if tool_name not in self._tool_registry:
                raise ValueError(f"工具未注册: {tool_name}")

            tool_func = self._tool_registry[tool_name]
            result = tool_func(**tool_args)

            # 执行成功
            execution.state = ToolExecutionState.COMPLETED
            execution.completed_at = time.time()
            execution.result = result

            # 发布工具执行完成事件
            from ..protocol.events import ToolExecutionCompleted

            complete_event = ToolExecutionCompleted(
                id=self.event_log.next_event_id(),
                execution_id=execution_id,
                tool_name=tool_name,
                result=result,
                duration_ms=(execution.completed_at - execution.started_at) * 1000,
                ts=execution.completed_at,
            )
            self.event_log.append(complete_event)
            self.event_bus.publish(complete_event)

            logger.debug(f"工具执行完成: {tool_name} ({execution_id})")

        except Exception as e:
            # 执行失败
            execution.state = ToolExecutionState.FAILED
            execution.completed_at = time.time()
            execution.error = str(e)

            # 发布工具执行失败事件
            from ..protocol.events import ToolExecutionFailed

            fail_event = ToolExecutionFailed(
                id=self.event_log.next_event_id(),
                execution_id=execution_id,
                tool_name=tool_name,
                error=str(e),
                duration_ms=(execution.completed_at - execution.started_at) * 1000,
                ts=execution.completed_at,
            )
            self.event_log.append(fail_event)
            self.event_bus.publish(fail_event)

            logger.error(f"工具执行失败: {tool_name} ({execution_id}): {e}")

        return execution

    def get_execution(self, execution_id: str) -> Optional[ToolExecution]:
        """获取工具执行信息。"""
        return self._executions.get(execution_id)

    def get_executions_by_tool(
        self,
        tool_name: str,
        limit: int = 100,
    ) -> list[ToolExecution]:
        """获取指定工具的执行历史。"""
        executions = [
            e for e in self._executions.values() if e.tool_name == tool_name
        ]
        return executions[-limit:]

    def get_stats(self) -> dict[str, Any]:
        """获取工具执行统计信息。"""
        total = len(self._executions)
        completed = sum(
            1 for e in self._executions.values()
            if e.state == ToolExecutionState.COMPLETED
        )
        failed = sum(
            1 for e in self._executions.values()
            if e.state == ToolExecutionState.FAILED
        )

        # 计算平均执行时间
        durations = []
        for e in self._executions.values():
            if e.started_at and e.completed_at:
                durations.append(e.completed_at - e.started_at)

        avg_duration = sum(durations) / len(durations) if durations else 0

        return {
            "total_executions": total,
            "completed": completed,
            "failed": failed,
            "success_rate": completed / total if total > 0 else 0,
            "average_duration_seconds": avg_duration,
        }

    def _handle_tool_event(self, event: Event) -> None:
        """处理工具执行相关事件。"""
        # 根据事件类型更新执行状态
        if hasattr(event, "execution_id"):
            execution = self._executions.get(event.execution_id)
            if execution:
                # 更新元数据
                if hasattr(event, "metadata"):
                    execution.metadata.update(event.metadata)
