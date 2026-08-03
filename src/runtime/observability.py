"""可观测性层（ObservabilityLayer）：全链路追踪+合规审计。

设计要点：
1. 全链路追踪
2. 合规审计日志
3. 与 G17 投影层整合
4. 与 OTel 导出整合
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from ..protocol import Event, EventType
from ..storage import EventLog
from .event_bus import EventBus

logger = logging.getLogger(__name__)


class TraceLevel(str, Enum):
    """追踪级别枚举。"""

    TRACE = "trace"
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AuditAction(str, Enum):
    """审计操作枚举。"""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    APPROVE = "approve"
    REJECT = "reject"
    DELETE = "delete"


@dataclass
class TraceSpan:
    """追踪跨度信息。"""

    span_id: str
    parent_span_id: Optional[str]
    operation: str
    start_time: float
    end_time: Optional[float] = None
    level: TraceLevel = TraceLevel.INFO
    status: str = "ok"
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AuditRecord:
    """审计记录。"""

    record_id: str
    timestamp: float
    action: AuditAction
    resource: str
    actor: str
    outcome: str
    details: dict[str, Any] = field(default_factory=dict)
    trace_id: Optional[str] = None


class ObservabilityLayer:
    """可观测性层：全链路追踪+合规审计。

    核心职责：
    1. 管理追踪跨度（Trace Spans）
    2. 记录审计日志
    3. 提供查询接口
    4. 与 OTel 导出整合
    """

    def __init__(
        self,
        event_log: EventLog,
        event_bus: EventBus,
    ) -> None:
        self.event_log = event_log
        self.event_bus = event_bus

        self._spans: dict[str, TraceSpan] = {}
        self._audit_records: list[AuditRecord] = []
        self._current_span_id: Optional[str] = None

        # 订阅所有事件用于追踪
        self.event_bus.subscribe(
            subscriber_id="observability",
            event_type="*",
            callback=self._track_event,
            priority=10,  # 低优先级，最后执行
        )

    def start_span(
        self,
        span_id: str,
        operation: str,
        *,
        parent_span_id: Optional[str] = None,
        level: TraceLevel = TraceLevel.INFO,
        attributes: Optional[dict[str, Any]] = None,
    ) -> TraceSpan:
        """开始追踪跨度。

        Args:
            span_id: 跨度 ID
            operation: 操作名称
            parent_span_id: 父跨度 ID
            level: 追踪级别
            attributes: 属性

        Returns:
            追踪跨度信息
        """
        span = TraceSpan(
            span_id=span_id,
            parent_span_id=parent_span_id,
            operation=operation,
            start_time=time.time(),
            level=level,
            attributes=attributes or {},
        )

        self._spans[span_id] = span
        self._current_span_id = span_id

        # 发布追踪开始事件
        from ..protocol.events import TraceSpanStarted

        event = TraceSpanStarted(
            id=self.event_log.next_event_id(),
            span_id=span_id,
            operation=operation,
            parent_span_id=parent_span_id,
            ts=span.start_time,
        )
        self.event_log.append(event)
        self.event_bus.publish(event)

        logger.debug(f"追踪跨度已开始: {span_id} ({operation})")
        return span

    def end_span(
        self,
        span_id: str,
        *,
        status: str = "ok",
        attributes: Optional[dict[str, Any]] = None,
    ) -> bool:
        """结束追踪跨度。

        Args:
            span_id: 跨度 ID
            status: 状态
            attributes: 更新的属性

        Returns:
            是否成功结束
        """
        span = self._spans.get(span_id)
        if not span:
            logger.warning(f"追踪跨度不存在: {span_id}")
            return False

        span.end_time = time.time()
        span.status = status

        if attributes:
            span.attributes.update(attributes)

        # 发布追踪结束事件
        from ..protocol.events import TraceSpanEnded

        event = TraceSpanEnded(
            id=self.event_log.next_event_id(),
            span_id=span_id,
            status=status,
            duration_ms=(span.end_time - span.start_time) * 1000,
            ts=span.end_time,
        )
        self.event_log.append(event)
        self.event_bus.publish(event)

        logger.debug(f"追踪跨度已结束: {span_id} (状态: {status})")
        return True

    def add_span_event(
        self,
        span_id: str,
        name: str,
        attributes: Optional[dict[str, Any]] = None,
    ) -> bool:
        """添加追踪事件。

        Args:
            span_id: 跨度 ID
            name: 事件名称
            attributes: 事件属性

        Returns:
            是否成功添加
        """
        span = self._spans.get(span_id)
        if not span:
            logger.warning(f"追踪跨度不存在: {span_id}")
            return False

        event_data = {
            "name": name,
            "timestamp": time.time(),
            "attributes": attributes or {},
        }
        span.events.append(event_data)

        logger.debug(f"追踪事件已添加: {span_id} ({name})")
        return True

    def record_audit(
        self,
        action: AuditAction,
        resource: str,
        actor: str,
        outcome: str,
        *,
        details: Optional[dict[str, Any]] = None,
        trace_id: Optional[str] = None,
    ) -> AuditRecord:
        """记录审计日志。

        Args:
            action: 审计操作
            resource: 资源
            actor: 操作者
            outcome: 结果
            details: 详细信息
            trace_id: 追踪 ID

        Returns:
            审计记录
        """
        record_id = f"audit_{self.event_log.next_event_id()}"
        record = AuditRecord(
            record_id=record_id,
            timestamp=time.time(),
            action=action,
            resource=resource,
            actor=actor,
            outcome=outcome,
            details=details or {},
            trace_id=trace_id,
        )

        self._audit_records.append(record)

        # 发布审计事件
        from ..protocol.events import AuditRecorded

        event = AuditRecorded(
            id=self.event_log.next_event_id(),
            record_id=record_id,
            action=action.value,
            resource=resource,
            actor=actor,
            outcome=outcome,
            ts=record.timestamp,
        )
        self.event_log.append(event)
        self.event_bus.publish(event)

        logger.debug(f"审计记录已创建: {record_id} ({action.value} {resource})")
        return record

    def get_span(self, span_id: str) -> Optional[TraceSpan]:
        """获取追踪跨度。"""
        return self._spans.get(span_id)

    def get_spans_by_operation(
        self,
        operation: str,
        limit: int = 100,
    ) -> list[TraceSpan]:
        """获取指定操作的追踪跨度。"""
        spans = [
            s for s in self._spans.values()
            if s.operation == operation
        ]
        return spans[-limit:]

    def get_audit_records(
        self,
        *,
        action: Optional[AuditAction] = None,
        resource: Optional[str] = None,
        actor: Optional[str] = None,
        limit: int = 100,
    ) -> list[AuditRecord]:
        """获取审计记录。"""
        records = self._audit_records

        if action:
            records = [r for r in records if r.action == action]
        if resource:
            records = [r for r in records if r.resource == resource]
        if actor:
            records = [r for r in records if r.actor == actor]

        return records[-limit:]

    def get_stats(self) -> dict[str, Any]:
        """获取可观测性统计信息。"""
        # 计算跨度统计
        span_count = len(self._spans)
        completed_spans = [
            s for s in self._spans.values() if s.end_time is not None
        ]
        avg_duration = 0
        if completed_spans:
            durations = [s.end_time - s.start_time for s in completed_spans]
            avg_duration = sum(durations) / len(durations)

        # 审计统计
        audit_count = len(self._audit_records)
        action_counts = {}
        for record in self._audit_records:
            action = record.action.value
            action_counts[action] = action_counts.get(action, 0) + 1

        return {
            "span_count": span_count,
            "completed_spans": len(completed_spans),
            "average_duration_seconds": avg_duration,
            "audit_record_count": audit_count,
            "audit_action_counts": action_counts,
        }

    def _track_event(self, event: Event) -> None:
        """追踪事件（自动创建追踪跨度）。"""
        # 为重要事件自动创建追踪跨度
        if event.event_type in (
            EventType.EXEC_APPROVAL_REQUEST,
            EventType.ITEM_COMPLETED,
            EventType.ERROR,
        ):
            span_id = f"event_{event.id}"
            self.start_span(
                span_id=span_id,
                operation=f"event.{event.event_type}",
                level=TraceLevel.INFO,
                attributes={"event_id": event.id, "event_type": event.event_type},
            )
            # 自动结束跨度
            self.end_span(span_id)
