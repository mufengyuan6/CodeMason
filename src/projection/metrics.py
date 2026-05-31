"""Metrics 指标投影（G17③ v1.23 落地：投影层——可查询观测）。

设计（design.md G17③）：
- 事件流写入时异步聚合 → {window:{session|task|loop}, metrics:{task_success_rate,
  tool_call_count/success_rate, latency_p50/p95, token_cost, failure_distribution},
  source_events:[可下钻溯源]}
- 接口 GET /api/metrics——从"事件日志"升级为"可查询指标"（蚂蚁财富 JD 口径）
- 纯投影零新存储，与"一切皆事件"同构（ctxrs/ctx 实证：结构化索引省 50× token）

范式声明：投影层 = 纯函数。
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Optional

from ..protocol import Event, EventType


@dataclass
class MetricsWindow:
    """一个聚合窗口（session/task/loop）。"""

    window_type: str  # session / task / loop
    window_id: str
    metrics: dict = field(default_factory=dict)
    source_events: list[int] = field(default_factory=list)  # 可下钻溯源

    def to_dict(self) -> dict:
        return {"window_type": self.window_type, "window_id": self.window_id, "metrics": self.metrics, "source_events": self.source_events[-100:]}


class MetricsProjector:
    """Metrics 指标投影：事件流 → 聚合指标（纯投影，可复算）。

    聚合维度（蚂蚁财富 JD 口径）：任务成功率 / 工具调用准确率 / 延迟 p50/p95 /
    成本（token）/ 失败类型分布。
    """

    def __init__(self, event_log=None) -> None:
        self.event_log = event_log
        self._windows: dict[str, MetricsWindow] = {}  # window_id → MetricsWindow

    # ---------- 聚合 ----------

    def aggregate(self, *, window_type: str = "session", window_id: str = "current") -> MetricsWindow:
        """从事件流聚合指标（纯投影：同事件流 → 同指标，可复算）。"""
        events = self.event_log.read_all() if self.event_log else []
        metrics = self._compute(events)
        window = MetricsWindow(window_type=window_type, window_id=window_id, metrics=metrics, source_events=[e.id for e in events])
        self._windows[window_id] = window
        return window

    def _compute(self, events: list[Event]) -> dict:
        """核心聚合（纯函数）。"""
        if not events:
            return {
                "task_count": 0, "task_success_rate": 0.0,
                "tool_call_count": 0, "tool_success_rate": 0.0,
                "latency_p50": 0.0, "latency_p95": 0.0,
                "token_cost": 0, "failure_distribution": {},
            }
        # 任务：ItemCompleted(turn_summary/task_result) 成功计数
        tasks = [e for e in events if e.type == EventType.ITEM_COMPLETED and getattr(e, "item_type", "") in ("turn_summary", "task_result")]
        task_success = sum(1 for e in tasks if self._is_success(e))
        # 工具：ItemCompleted(tool_result)
        tools = [e for e in events if e.type == EventType.ITEM_COMPLETED and getattr(e, "item_type", "") == "tool_result"]
        tool_success = sum(1 for e in tools if self._is_success(e))
        # 失败分布
        failures: dict[str, int] = {}
        for e in events:
            if e.type == EventType.ERROR:
                et = getattr(e, "error_type", "unknown")
                failures[et] = failures.get(et, 0) + 1
            elif e.type == EventType.ITEM_COMPLETED and not self._is_success(e):
                failures["tool_failed"] = failures.get("tool_failed", 0) + 1
        # 延迟：事件间间隔
        timestamps = sorted(e.ts for e in events)
        latencies = [b - a for a, b in zip(timestamps, timestamps[1:])] if len(timestamps) > 1 else []
        # token 成本（content.tokens 汇总）
        token_cost = sum(int((getattr(e, "content", {}) or {}).get("tokens", 0) or 0) for e in events if isinstance(getattr(e, "content", None), dict))
        return {
            "task_count": len(tasks),
            "task_success_rate": round(task_success / max(len(tasks), 1), 3),
            "tool_call_count": len(tools),
            "tool_success_rate": round(tool_success / max(len(tools), 1), 3),
            "latency_p50": round(statistics.median(latencies), 3) if latencies else 0.0,
            "latency_p95": round(self._p95(latencies), 3) if latencies else 0.0,
            "token_cost": token_cost,
            "failure_distribution": failures,
        }

    @staticmethod
    def _is_success(ev: Event) -> bool:
        content = getattr(ev, "content", None) or {}
        if isinstance(content, dict):
            if content.get("status") in ("completed", "ok", "passed", "success"):
                return True
            if content.get("status") in ("error", "failed", "blocked"):
                return False
            if "error" in content:
                return False
        return True  # 无状态标记默认成功（保守）

    @staticmethod
    def _p95(values: list[float]) -> float:
        if not values:
            return 0.0
        idx = min(len(values) - 1, int(len(values) * 0.95))
        return sorted(values)[idx]

    # ---------- 查询 ----------

    def get(self, window_id: str) -> Optional[MetricsWindow]:
        return self._windows.get(window_id)

    def report(self) -> dict:
        """驾驶舱展示（/api/metrics 数据源）。"""
        w = self.aggregate()
        return {"window": w.to_dict()}
