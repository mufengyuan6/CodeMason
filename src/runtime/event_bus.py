"""统一事件总线（EventBus）：所有模块通过事件通信，事件是唯一通信方式。

设计要点：
1. 事件是唯一通信方式（CodeMason 核心叙事"一切皆事件"）
2. 所有状态变更通过事件记录
3. 可观测性天然支持全链路追踪
4. 支持同步和异步事件分发
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..protocol import Event, EventType

logger = logging.getLogger(__name__)


class EventBusError(Exception):
    """事件总线错误。"""


@dataclass
class EventSubscription:
    """事件订阅信息。"""

    subscriber_id: str
    event_type: str
    callback: Callable[[Event], None]
    priority: int = 0  # 优先级，数字越大越先执行
    is_async: bool = False  # 是否异步回调


class EventBus:
    """统一事件总线：所有模块通过事件通信。

    核心职责：
    - 事件分发：将事件分发给所有订阅者
    - 事件过滤：支持按事件类型过滤
    - 事件优先级：支持优先级队列
    - 异步支持：支持异步事件分发
    - 追踪支持：记录事件分发历史，支持全链路追踪
    """

    def __init__(self, max_history: int = 1000) -> None:
        self._subscriptions: dict[str, list[EventSubscription]] = {}
        self._history: list[Event] = []
        self._max_history = max_history
        self._lock = threading.RLock()
        self._dispatch_count = 0
        self._error_count = 0
        self._listeners: list[Callable[[Event], None]] = []

    def subscribe(
        self,
        subscriber_id: str,
        event_type: str,
        callback: Callable[[Event], None],
        priority: int = 0,
        is_async: bool = False,
    ) -> None:
        """订阅事件。

        Args:
            subscriber_id: 订阅者 ID
            event_type: 事件类型（'*' 表示订阅所有事件）
            callback: 回调函数
            priority: 优先级（数字越大越先执行）
            is_async: 是否异步回调
        """
        with self._lock:
            subscription = EventSubscription(
                subscriber_id=subscriber_id,
                event_type=event_type,
                callback=callback,
                priority=priority,
                is_async=is_async,
            )

            if event_type not in self._subscriptions:
                self._subscriptions[event_type] = []

            # 按优先级插入
            subs = self._subscriptions[event_type]
            inserted = False
            for i, sub in enumerate(subs):
                if priority > sub.priority:
                    subs.insert(i, subscription)
                    inserted = True
                    break
            if not inserted:
                subs.append(subscription)

            logger.debug(
                f"订阅已注册: {subscriber_id} -> {event_type} (优先级: {priority})"
            )

    def unsubscribe(self, subscriber_id: str, event_type: str) -> bool:
        """取消订阅。

        Args:
            subscriber_id: 订阅者 ID
            event_type: 事件类型

        Returns:
            是否成功取消
        """
        with self._lock:
            if event_type not in self._subscriptions:
                return False

            subs = self._subscriptions[event_type]
            original_count = len(subs)
            self._subscriptions[event_type] = [
                s for s in subs if s.subscriber_id != subscriber_id
            ]

            removed = original_count - len(self._subscriptions[event_type])
            if removed > 0:
                logger.debug(f"订阅已取消: {subscriber_id} -> {event_type}")

            return removed > 0

    def publish(self, event: Event) -> None:
        """发布事件（同步分发）。

        Args:
            event: 要发布的事件
        """
        with self._lock:
            # 记录事件历史
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

            # 获取所有匹配的订阅者
            subscriptions = self._get_matching_subscriptions(event)

            # 按优先级分发
            for sub in subscriptions:
                try:
                    if sub.is_async:
                        # 异步回调在新线程中执行
                        threading.Thread(
                            target=self._safe_call,
                            args=(sub, event),
                            daemon=True,
                        ).start()
                    else:
                        self._safe_call(sub, event)
                except Exception as e:
                    logger.error(f"事件分发失败: {sub.subscriber_id} -> {event.event_type}: {e}")
                    self._error_count += 1

            self._dispatch_count += 1

            # 通知全局监听器
            for listener in self._listeners:
                try:
                    listener(event)
                except Exception as e:
                    logger.error(f"全局监听器通知失败: {e}")

    def _safe_call(self, sub: EventSubscription, event: Event) -> None:
        """安全调用回调函数。"""
        try:
            sub.callback(event)
        except Exception as e:
            logger.error(f"回调执行失败: {sub.subscriber_id}: {e}")
            self._error_count += 1

    def _get_matching_subscriptions(self, event: Event) -> list[EventSubscription]:
        """获取匹配事件的订阅者列表。"""
        subscriptions = []

        # 精确匹配（Event 类使用 type 字段）
        event_type = event.type.value if hasattr(event.type, 'value') else str(event.type)
        if event_type in self._subscriptions:
            subscriptions.extend(self._subscriptions[event_type])

        # 通配符匹配（'*'）
        if "*" in self._subscriptions:
            subscriptions.extend(self._subscriptions["*"])

        # 去重（同一个订阅者可能通过不同方式匹配）
        seen = set()
        unique_subs = []
        for sub in subscriptions:
            if sub.subscriber_id not in seen:
                seen.add(sub.subscriber_id)
                unique_subs.append(sub)

        return unique_subs

    def add_listener(self, listener: Callable[[Event], None]) -> None:
        """添加全局事件监听器（用于追踪和审计）。"""
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[Event], None]) -> None:
        """移除全局事件监听器。"""
        self._listeners.remove(listener)

    def get_history(
        self,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[Event]:
        """获取事件历史。

        Args:
            event_type: 事件类型过滤（None 表示所有）
            limit: 返回数量限制

        Returns:
            事件列表（按时间顺序）
        """
        with self._lock:
            if event_type is None:
                return self._history[-limit:]

            # Event 类使用 type 字段，需要获取枚举值
            return [
                e for e in self._history
                if (e.type.value if hasattr(e.type, 'value') else str(e.type)) == event_type
            ][-limit:]

    def get_stats(self) -> dict[str, Any]:
        """获取事件总线统计信息。"""
        with self._lock:
            return {
                "dispatch_count": self._dispatch_count,
                "error_count": self._error_count,
                "history_size": len(self._history),
                "subscription_count": sum(
                    len(subs) for subs in self._subscriptions.values()
                ),
                "event_type_counts": {
                    et: len(subs) for et, subs in self._subscriptions.items()
                },
            }

    def clear_history(self) -> None:
        """清空事件历史。"""
        with self._lock:
            self._history.clear()
            logger.info("事件历史已清空")
