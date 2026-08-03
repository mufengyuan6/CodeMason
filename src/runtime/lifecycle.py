"""生命周期管理器（RuntimeLifecycle）：会话/状态/上下文注入的统一管理。

设计要点：
1. 会话生命周期管理（创建/恢复/销毁）
2. 状态管理（基于事件投影）
3. 上下文注入（统一注入策略）
4. 与 AgentLoop 和 EventLog 集成
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


class SessionState(str, Enum):
    """会话状态枚举。"""

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    DESTROYED = "destroyed"


@dataclass
class SessionInfo:
    """会话信息。"""

    session_id: str
    state: SessionState
    created_at: float
    updated_at: float
    metadata: dict[str, Any] = field(default_factory=dict)
    event_count: int = 0
    last_event_id: Optional[int] = None


class RuntimeLifecycle:
    """生命周期管理器：会话/状态/上下文注入的统一管理。

    核心职责：
    1. 会话生命周期管理（创建/恢复/销毁）
    2. 状态管理（基于事件投影）
    3. 上下文注入（统一注入策略）
    4. 事件驱动的状态转换
    """

    def __init__(
        self,
        event_log: EventLog,
        event_bus: EventBus,
    ) -> None:
        self.event_log = event_log
        self.event_bus = event_bus
        self._sessions: dict[str, SessionInfo] = {}
        self._current_session: Optional[str] = None

        # 订阅会话相关事件
        self.event_bus.subscribe(
            subscriber_id="lifecycle",
            event_type="*",
            callback=self._handle_event,
            priority=100,  # 高优先级，确保状态及时更新
        )

    def create_session(
        self,
        session_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SessionInfo:
        """创建新会话。

        Args:
            session_id: 会话 ID
            metadata: 会话元数据

        Returns:
            会话信息
        """
        now = time.time()
        session = SessionInfo(
            session_id=session_id,
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )

        self._sessions[session_id] = session
        self._current_session = session_id

        # 发布会话创建事件
        from ..protocol.events import SessionCreated

        event = SessionCreated(
            id=self.event_log.next_event_id(),
            session_id=session_id,
            ts=now,
        )
        self.event_log.append(event)
        self.event_bus.publish(event)

        logger.info(f"会话已创建: {session_id}")
        return session

    def get_session(self, session_id: str) -> Optional[SessionInfo]:
        """获取会话信息。"""
        return self._sessions.get(session_id)

    def get_current_session(self) -> Optional[SessionInfo]:
        """获取当前会话。"""
        if self._current_session:
            return self._sessions.get(self._current_session)
        return None

    def update_session_state(
        self,
        session_id: str,
        new_state: SessionState,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """更新会话状态。

        Args:
            session_id: 会话 ID
            new_state: 新状态
            metadata: 更新的元数据

        Returns:
            是否成功更新
        """
        session = self._sessions.get(session_id)
        if not session:
            logger.warning(f"会话不存在: {session_id}")
            return False

        old_state = session.state
        session.state = new_state
        session.updated_at = time.time()

        if metadata:
            session.metadata.update(metadata)

        # 发布状态变更事件
        from ..protocol.events import SessionStateChanged

        event = SessionStateChanged(
            id=self.event_log.next_event_id(),
            session_id=session_id,
            old_state=old_state.value,
            new_state=new_state.value,
            ts=session.updated_at,
        )
        self.event_log.append(event)
        self.event_bus.publish(event)

        logger.info(f"会话状态已更新: {session_id} {old_state.value} -> {new_state.value}")
        return True

    def destroy_session(self, session_id: str) -> bool:
        """销毁会话。

        Args:
            session_id: 会话 ID

        Returns:
            是否成功销毁
        """
        session = self._sessions.get(session_id)
        if not session:
            logger.warning(f"会话不存在: {session_id}")
            return False

        # 更新状态为销毁
        self.update_session_state(session_id, SessionState.DESTROYED)

        # 发布会话销毁事件
        from ..protocol.events import SessionDestroyed

        event = SessionDestroyed(
            id=self.event_log.next_event_id(),
            session_id=session_id,
            ts=time.time(),
        )
        self.event_log.append(event)
        self.event_bus.publish(event)

        # 清理会话
        if self._current_session == session_id:
            self._current_session = None

        logger.info(f"会话已销毁: {session_id}")
        return True

    def inject_context(
        self,
        session_id: str,
        context_type: str,
        context_data: dict[str, Any],
    ) -> None:
        """注入上下文。

        Args:
            session_id: 会话 ID
            context_type: 上下文类型（如 "memory", "plan", "state"）
            context_data: 上下文数据
        """
        session = self._sessions.get(session_id)
        if not session:
            logger.warning(f"会话不存在: {session_id}")
            return

        # 发布上下文注入事件
        from ..protocol.events import ContextInjected

        event = ContextInjected(
            id=self.event_log.next_event_id(),
            session_id=session_id,
            context_type=context_type,
            context_data=context_data,
            ts=time.time(),
        )
        self.event_log.append(event)
        self.event_bus.publish(event)

        logger.debug(f"上下文已注入: {session_id} -> {context_type}")

    def _handle_event(self, event: Event) -> None:
        """处理事件（更新会话状态）。"""
        # 根据事件类型更新会话状态
        if hasattr(event, "session_id"):
            session = self._sessions.get(event.session_id)
            if session:
                session.event_count += 1
                session.last_event_id = event.id
                session.updated_at = time.time()

    def get_session_stats(self, session_id: str) -> dict[str, Any]:
        """获取会话统计信息。"""
        session = self._sessions.get(session_id)
        if not session:
            return {}

        return {
            "session_id": session.session_id,
            "state": session.state.value,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "event_count": session.event_count,
            "last_event_id": session.last_event_id,
            "metadata": session.metadata,
        }
