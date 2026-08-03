"""多Agent协调器（AgentCoordinator）：任务分发+结果合并+冲突解决。

设计要点：
1. 任务分发机制
2. 结果合并策略
3. 冲突解决逻辑
4. 与 Worktrees 和 Team Kernel 整合
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from ..protocol import Event, EventType
from ..storage import EventLog
from .event_bus import EventBus

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """任务状态枚举。"""

    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ConflictResolution(str, Enum):
    """冲突解决策略枚举。"""

    LAST_WRITE_WINS = "last_write_wins"
    FIRST_WRITE_WINS = "first_write_wins"
    MERGE = "merge"
    ESCALATE = "escalate"


@dataclass
class AgentTask:
    """Agent 任务信息。"""

    task_id: str
    description: str
    assigned_to: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)


@dataclass
class ConflictInfo:
    """冲突信息。"""

    conflict_id: str
    task_ids: list[str]
    resource: str
    resolution: ConflictResolution
    resolved_at: Optional[float] = None
    resolved_by: Optional[str] = None


class AgentCoordinator:
    """多Agent协调器：任务分发+结果合并+冲突解决。

    核心职责：
    1. 管理多Agent任务分配
    2. 处理任务依赖和并行
    3. 解决资源冲突
    4. 合并执行结果
    """

    def __init__(
        self,
        event_log: EventLog,
        event_bus: EventBus,
        conflict_resolution: ConflictResolution = ConflictResolution.LAST_WRITE_WINS,
    ) -> None:
        self.event_log = event_log
        self.event_bus = event_bus
        self.conflict_resolution = conflict_resolution

        self._tasks: dict[str, AgentTask] = {}
        self._agent_tasks: dict[str, list[str]] = {}  # agent_id -> task_ids
        self._conflicts: dict[str, ConflictInfo] = {}

        # 订阅相关事件
        self.event_bus.subscribe(
            subscriber_id="coordinator",
            event_type="agent.*",
            callback=self._handle_agent_event,
            priority=30,
        )

    def create_task(
        self,
        task_id: str,
        description: str,
        *,
        assigned_to: Optional[str] = None,
        dependencies: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> AgentTask:
        """创建任务。

        Args:
            task_id: 任务 ID
            description: 任务描述
            assigned_to: 分配给哪个 Agent
            dependencies: 依赖的任务 ID 列表
            metadata: 元数据

        Returns:
            任务信息
        """
        task = AgentTask(
            task_id=task_id,
            description=description,
            assigned_to=assigned_to,
            dependencies=dependencies or [],
            metadata=metadata or {},
        )

        self._tasks[task_id] = task

        # 如果指定了 Agent，更新分配关系
        if assigned_to:
            if assigned_to not in self._agent_tasks:
                self._agent_tasks[assigned_to] = []
            self._agent_tasks[assigned_to].append(task_id)
            task.status = TaskStatus.ASSIGNED

        # 发布任务创建事件
        from ..protocol.events import TaskCreated

        event = TaskCreated(
            id=self.event_log.next_event_id(),
            task_id=task_id,
            description=description,
            assigned_to=assigned_to,
            ts=time.time(),
        )
        self.event_log.append(event)
        self.event_bus.publish(event)

        logger.info(f"任务已创建: {task_id} (分配给: {assigned_to or '未分配'})")
        return task

    def assign_task(self, task_id: str, agent_id: str) -> bool:
        """分配任务给 Agent。

        Args:
            task_id: 任务 ID
            agent_id: Agent ID

        Returns:
            是否成功分配
        """
        task = self._tasks.get(task_id)
        if not task:
            logger.warning(f"任务不存在: {task_id}")
            return False

        # 检查依赖是否满足
        if not self._check_dependencies(task):
            logger.warning(f"任务依赖未满足: {task_id}")
            return False

        # 更新分配
        task.assigned_to = agent_id
        task.status = TaskStatus.ASSIGNED

        if agent_id not in self._agent_tasks:
            self._agent_tasks[agent_id] = []
        if task_id not in self._agent_tasks[agent_id]:
            self._agent_tasks[agent_id].append(task_id)

        # 发布任务分配事件
        from ..protocol.events import TaskAssigned

        event = TaskAssigned(
            id=self.event_log.next_event_id(),
            task_id=task_id,
            agent_id=agent_id,
            ts=time.time(),
        )
        self.event_log.append(event)
        self.event_bus.publish(event)

        logger.info(f"任务已分配: {task_id} -> {agent_id}")
        return True

    def start_task(self, task_id: str) -> bool:
        """开始执行任务。

        Args:
            task_id: 任务 ID

        Returns:
            是否成功开始
        """
        task = self._tasks.get(task_id)
        if not task:
            logger.warning(f"任务不存在: {task_id}")
            return False

        if task.status != TaskStatus.ASSIGNED:
            logger.warning(f"任务状态不允许开始: {task_id} (当前状态: {task.status})")
            return False

        task.status = TaskStatus.RUNNING
        task.started_at = time.time()

        # 发布任务开始事件
        from ..protocol.events import TaskStarted

        event = TaskStarted(
            id=self.event_log.next_event_id(),
            task_id=task_id,
            agent_id=task.assigned_to,
            ts=task.started_at,
        )
        self.event_log.append(event)
        self.event_bus.publish(event)

        logger.info(f"任务已开始: {task_id}")
        return True

    def complete_task(
        self,
        task_id: str,
        result: dict[str, Any],
    ) -> bool:
        """完成任务。

        Args:
            task_id: 任务 ID
            result: 执行结果

        Returns:
            是否成功完成
        """
        task = self._tasks.get(task_id)
        if not task:
            logger.warning(f"任务不存在: {task_id}")
            return False

        task.status = TaskStatus.COMPLETED
        task.completed_at = time.time()
        task.result = result

        # 发布任务完成事件
        from ..protocol.events import TaskCompleted

        event = TaskCompleted(
            id=self.event_log.next_event_id(),
            task_id=task_id,
            agent_id=task.assigned_to,
            result=result,
            duration_ms=(task.completed_at - (task.started_at or task.created_at)) * 1000,
            ts=task.completed_at,
        )
        self.event_log.append(event)
        self.event_bus.publish(event)

        logger.info(f"任务已完成: {task_id}")
        return True

    def fail_task(self, task_id: str, error: str) -> bool:
        """标记任务失败。

        Args:
            task_id: 任务 ID
            error: 错误信息

        Returns:
            是否成功标记
        """
        task = self._tasks.get(task_id)
        if not task:
            logger.warning(f"任务不存在: {task_id}")
            return False

        task.status = TaskStatus.FAILED
        task.completed_at = time.time()
        task.error = error

        # 发布任务失败事件
        from ..protocol.events import TaskFailed

        event = TaskFailed(
            id=self.event_log.next_event_id(),
            task_id=task_id,
            agent_id=task.assigned_to,
            error=error,
            ts=task.completed_at,
        )
        self.event_log.append(event)
        self.event_bus.publish(event)

        logger.error(f"任务已失败: {task_id}: {error}")
        return True

    def get_task(self, task_id: str) -> Optional[AgentTask]:
        """获取任务信息。"""
        return self._tasks.get(task_id)

    def get_agent_tasks(self, agent_id: str) -> list[AgentTask]:
        """获取 Agent 的任务列表。"""
        task_ids = self._agent_tasks.get(agent_id, [])
        return [self._tasks[tid] for tid in task_ids if tid in self._tasks]

    def get_pending_tasks(self) -> list[AgentTask]:
        """获取待处理任务列表。"""
        return [
            t for t in self._tasks.values()
            if t.status in (TaskStatus.PENDING, TaskStatus.ASSIGNED)
        ]

    def get_stats(self) -> dict[str, Any]:
        """获取协调器统计信息。"""
        total = len(self._tasks)
        by_status = {}
        for task in self._tasks.values():
            status = task.status.value
            by_status[status] = by_status.get(status, 0) + 1

        return {
            "total_tasks": total,
            "by_status": by_status,
            "active_agents": len(self._agent_tasks),
            "conflicts": len(self._conflicts),
        }

    def _check_dependencies(self, task: AgentTask) -> bool:
        """检查任务依赖是否满足。"""
        for dep_id in task.dependencies:
            dep_task = self._tasks.get(dep_id)
            if not dep_task or dep_task.status != TaskStatus.COMPLETED:
                return False
        return True

    def _handle_agent_event(self, event: Event) -> None:
        """处理 Agent 相关事件。"""
        # 根据事件类型更新任务状态
        if hasattr(event, "task_id"):
            task = self._tasks.get(event.task_id)
            if task:
                # 更新元数据
                if hasattr(event, "metadata"):
                    task.metadata.update(event.metadata)
