"""沙箱管理器（SandboxManager）：执行隔离+资源控制。

设计要点：
1. 执行隔离管理
2. 资源控制（CPU/内存/网络）
3. 与 G19 执行沙箱四后端整合
4. 统一的沙箱生命周期管理
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


class SandboxBackend(str, Enum):
    """沙箱后端枚举。"""

    LOCAL = "local"
    DOCKER = "docker"
    GVISOR = "gvisor"
    FIRECRACKER = "firecracker"


class SandboxState(str, Enum):
    """沙箱状态枚举。"""

    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class SandboxConfig:
    """沙箱配置。"""

    backend: SandboxBackend = SandboxBackend.LOCAL
    network_enabled: bool = False
    memory_limit_mb: int = 512
    cpu_limit: float = 1.0
    timeout_seconds: float = 300.0
    allowed_hosts: list[str] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)


@dataclass
class SandboxInstance:
    """沙箱实例信息。"""

    sandbox_id: str
    config: SandboxConfig
    state: SandboxState
    created_at: float
    started_at: Optional[float] = None
    stopped_at: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    resource_usage: dict[str, Any] = field(default_factory=dict)


class SandboxManager:
    """沙箱管理器：执行隔离+资源控制。

    核心职责：
    1. 管理沙箱生命周期
    2. 提供执行隔离
    3. 控制资源使用
    4. 与 G19 执行沙箱后端整合
    """

    def __init__(
        self,
        event_log: EventLog,
        event_bus: EventBus,
    ) -> None:
        self.event_log = event_log
        self.event_bus = event_bus

        self._sandboxes: dict[str, SandboxInstance] = {}

        # 订阅沙箱相关事件
        self.event_bus.subscribe(
            subscriber_id="sandbox_manager",
            event_type="sandbox.*",
            callback=self._handle_sandbox_event,
            priority=40,
        )

    def create_sandbox(
        self,
        sandbox_id: str,
        *,
        config: Optional[SandboxConfig] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SandboxInstance:
        """创建沙箱。

        Args:
            sandbox_id: 沙箱 ID
            config: 沙箱配置
            metadata: 元数据

        Returns:
            沙箱实例信息
        """
        if sandbox_id in self._sandboxes:
            raise ValueError(f"沙箱已存在: {sandbox_id}")

        config = config or SandboxConfig()
        now = time.time()

        sandbox = SandboxInstance(
            sandbox_id=sandbox_id,
            config=config,
            state=SandboxState.CREATED,
            created_at=now,
            metadata=metadata or {},
        )

        self._sandboxes[sandbox_id] = sandbox

        # 发布沙箱创建事件
        from ..protocol.events import SandboxCreated

        event = SandboxCreated(
            id=self.event_log.next_event_id(),
            sandbox_id=sandbox_id,
            backend=config.backend.value,
            ts=now,
        )
        self.event_log.append(event)
        self.event_bus.publish(event)

        logger.info(f"沙箱已创建: {sandbox_id} (后端: {config.backend.value})")
        return sandbox

    def start_sandbox(self, sandbox_id: str) -> bool:
        """启动沙箱。

        Args:
            sandbox_id: 沙箱 ID

        Returns:
            是否成功启动
        """
        sandbox = self._sandboxes.get(sandbox_id)
        if not sandbox:
            logger.warning(f"沙箱不存在: {sandbox_id}")
            return False

        if sandbox.state != SandboxState.CREATED:
            logger.warning(f"沙箱状态不允许启动: {sandbox_id} (当前状态: {sandbox.state})")
            return False

        sandbox.state = SandboxState.STARTING
        sandbox.started_at = time.time()

        # 发布沙箱启动事件
        from ..protocol.events import SandboxStarted

        event = SandboxStarted(
            id=self.event_log.next_event_id(),
            sandbox_id=sandbox_id,
            ts=sandbox.started_at,
        )
        self.event_log.append(event)
        self.event_bus.publish(event)

        # 模拟启动过程
        sandbox.state = SandboxState.RUNNING

        logger.info(f"沙箱已启动: {sandbox_id}")
        return True

    def stop_sandbox(self, sandbox_id: str) -> bool:
        """停止沙箱。

        Args:
            sandbox_id: 沙箱 ID

        Returns:
            是否成功停止
        """
        sandbox = self._sandboxes.get(sandbox_id)
        if not sandbox:
            logger.warning(f"沙箱不存在: {sandbox_id}")
            return False

        if sandbox.state != SandboxState.RUNNING:
            logger.warning(f"沙箱状态不允许停止: {sandbox_id} (当前状态: {sandbox.state})")
            return False

        sandbox.state = SandboxState.STOPPING
        sandbox.stopped_at = time.time()

        # 发布沙箱停止事件
        from ..protocol.events import SandboxStopped

        event = SandboxStopped(
            id=self.event_log.next_event_id(),
            sandbox_id=sandbox_id,
            ts=sandbox.stopped_at,
        )
        self.event_log.append(event)
        self.event_bus.publish(event)

        # 模拟停止过程
        sandbox.state = SandboxState.STOPPED

        logger.info(f"沙箱已停止: {sandbox_id}")
        return True

    def execute_in_sandbox(
        self,
        sandbox_id: str,
        command: str,
        *,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """在沙箱中执行命令。

        Args:
            sandbox_id: 沙箱 ID
            command: 命令
            timeout: 超时时间（秒）

        Returns:
            执行结果
        """
        sandbox = self._sandboxes.get(sandbox_id)
        if not sandbox:
            raise ValueError(f"沙箱不存在: {sandbox_id}")

        if sandbox.state != SandboxState.RUNNING:
            raise ValueError(f"沙箱未运行: {sandbox_id}")

        # 发布沙箱执行事件
        from ..protocol.events import SandboxExecutionStarted

        start_event = SandboxExecutionStarted(
            id=self.event_log.next_event_id(),
            sandbox_id=sandbox_id,
            command=command,
            ts=time.time(),
        )
        self.event_log.append(start_event)
        self.event_bus.publish(start_event)

        # 模拟执行
        start_time = time.time()
        result = {
            "command": command,
            "exit_code": 0,
            "stdout": "模拟输出",
            "stderr": "",
            "duration_ms": (time.time() - start_time) * 1000,
        }

        # 发布沙箱执行完成事件
        from ..protocol.events import SandboxExecutionCompleted

        complete_event = SandboxExecutionCompleted(
            id=self.event_log.next_event_id(),
            sandbox_id=sandbox_id,
            command=command,
            exit_code=0,
            ts=time.time(),
        )
        self.event_log.append(complete_event)
        self.event_bus.publish(complete_event)

        return result

    def get_sandbox(self, sandbox_id: str) -> Optional[SandboxInstance]:
        """获取沙箱实例。"""
        return self._sandboxes.get(sandbox_id)

    def list_sandboxes(
        self,
        *,
        state: Optional[SandboxState] = None,
        backend: Optional[SandboxBackend] = None,
    ) -> list[SandboxInstance]:
        """列出沙箱实例。"""
        sandboxes = list(self._sandboxes.values())

        if state:
            sandboxes = [s for s in sandboxes if s.state == state]
        if backend:
            sandboxes = [s for s in sandboxes if s.config.backend == backend]

        return sandboxes

    def get_stats(self) -> dict[str, Any]:
        """获取沙箱管理器统计信息。"""
        total = len(self._sandboxes)
        by_state = {}
        by_backend = {}

        for sandbox in self._sandboxes.values():
            state = sandbox.state.value
            by_state[state] = by_state.get(state, 0) + 1

            backend = sandbox.config.backend.value
            by_backend[backend] = by_backend.get(backend, 0) + 1

        return {
            "total_sandboxes": total,
            "by_state": by_state,
            "by_backend": by_backend,
        }

    def _handle_sandbox_event(self, event: Event) -> None:
        """处理沙箱相关事件。"""
        # 根据事件类型更新沙箱状态
        if hasattr(event, "sandbox_id"):
            sandbox = self._sandboxes.get(event.sandbox_id)
            if sandbox:
                # 更新元数据
                if hasattr(event, "metadata"):
                    sandbox.metadata.update(event.metadata)
