"""健康信号（v1.13 核心，3.2 健康信号贯穿）——卡检测 stuck + 会话健康度。

- **卡检测联动（对标 OpenHands StuckDetector）**：检测重复循环（相同工具调用/相同错误
  反复出现 = 上下文没有新信息或假设固化）→ 触发上下文干预：强制压缩 / 派 fresh-context
  子代理 / 提示用户
- **会话健康度驱动生命周期**：上下文四维指标连续恶化（回捞激增 / 摘要遗漏上升 / stale
  命中升高）+ stuck 频率上升 → 健康度报警 → Web 面板提示"建议交接或开新会话"

作为 Hook 消费者注册（on_tool_call / on_failure / PostToolUse），一次解决两个差距
（Hook 7 事件点 + 健康信号）。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class StuckSignal:
    """卡检测结果。"""

    stuck: bool
    reason: str
    repeated_tool: str = ""
    repeat_count: int = 0
    suggestion: str = ""


@dataclass
class HealthReport:
    """会话健康度报告（驱动生命周期决策）。"""

    score: float = 100.0           # 0-100，越高越健康
    stuck_count: int = 0
    refetch_rate: float = 0.0      # 回捞激增（四维指标 1）
    summary_miss: int = 0          # 摘要遗漏上升（四维指标 2）
    stale_hit_rate: float = 0.0    # stale 命中升高（四维指标 3）
    level: str = "healthy"         # healthy / degraded / critical
    advice: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 1),
            "stuck_count": self.stuck_count,
            "refetch_rate": round(self.refetch_rate, 3),
            "summary_miss": self.summary_miss,
            "stale_hit_rate": round(self.stale_hit_rate, 3),
            "level": self.level,
            "advice": self.advice,
            "updated_at": self.updated_at,
        }


class StuckDetector:
    """卡检测：重复工具调用 / 重复错误 → stuck 判定（OpenHands StuckDetector 机制化）。"""

    def __init__(self, window: int = 8, repeat_threshold: int = 3) -> None:
        self.window = window
        self.repeat_threshold = repeat_threshold
        self._tool_calls: deque[str] = deque(maxlen=window)
        self._errors: deque[str] = deque(maxlen=window)

    def observe_tool_call(self, tool_name: str, args: dict) -> Optional[StuckSignal]:
        """观察工具调用（PostToolUse）：相同工具 + 相同关键参数反复出现 = stuck。"""
        key = f"{tool_name}:{self._args_key(args)}"
        self._tool_calls.append(key)
        count = list(self._tool_calls).count(key)
        if count >= self.repeat_threshold:
            return StuckSignal(
                stuck=True,
                reason=f"工具 {tool_name} 相同调用重复 {count} 次（最近 {self.window} 次调用内）",
                repeated_tool=tool_name,
                repeat_count=count,
                suggestion="强制压缩上下文 / 派 fresh-context 子代理 / 提示用户重新描述目标",
            )
        return None

    def observe_error(self, error_type: str, message: str = "") -> Optional[StuckSignal]:
        """观察错误（on_failure）：相同错误反复出现 = 假设固化。"""
        key = f"{error_type}:{message[:60]}"
        self._errors.append(key)
        count = list(self._errors).count(key)
        if count >= self.repeat_threshold:
            return StuckSignal(
                stuck=True,
                reason=f"相同错误重复 {count} 次（{error_type}: {message[:60]}）",
                repeated_tool=error_type,
                repeat_count=count,
                suggestion="重新调查根因（superpowers 四阶段）/ 质疑当前假设 / 提交人类审查",
            )
        return None

    def stuck_count(self) -> int:
        """当前窗口内的 stuck 信号数（按 distinct key 计，不重复计数）。

        同一工具+同一参数重复 3 次 = 1 个 stuck 信号（不是 3 个）——
        健康度扣分按"独立卡点"数，防重复调用把分数打到 0。
        """
        keys = list(self._tool_calls)
        return len({k for k in keys if keys.count(k) >= self.repeat_threshold})

    @staticmethod
    def _args_key(args: dict) -> str:
        """参数签名：只取命令/路径类关键参数（防同工具不同操作误判）。"""
        keys = ("command", "path", "file_path", "pattern", "query")
        return "|".join(f"{k}={str(args.get(k, ''))[:40]}" for k in keys if args.get(k))


class SessionHealth:
    """会话健康度：四维指标聚合 → 评分 → 生命周期建议。

    四维指标（design G7）：回捞次数（refetch_rate）/ stale 命中率 / 摘要遗漏数 / 压缩比。
    连续恶化 + stuck 频率上升 → 健康度报警 → 建议交接/新会话（Windsurf 实践机制化）。
    """

    def __init__(self, stuck_detector: Optional[StuckDetector] = None) -> None:
        self.stuck = stuck_detector or StuckDetector()
        self._refetch_history: deque[float] = deque(maxlen=10)
        self._stale_history: deque[float] = deque(maxlen=10)
        self._summary_miss_total = 0
        self._report: Optional[HealthReport] = None

    def observe_tool_call(self, tool_name: str, args: dict) -> Optional[StuckSignal]:
        """Hook 消费者入口（PostToolUse/on_tool_call）。"""
        return self.stuck.observe_tool_call(tool_name, args)

    def observe_error(self, error_type: str, message: str = "") -> Optional[StuckSignal]:
        """Hook 消费者入口（on_failure）。"""
        return self.stuck.observe_error(error_type, message)

    def observe_recall(self, refetch_rate: float) -> None:
        """回捞指标更新（EventRecall 服务反哺）。"""
        self._refetch_history.append(refetch_rate)

    def observe_stale_hit(self, rate: float) -> None:
        """stale 命中率更新（失效传播管道反哺）。"""
        self._stale_history.append(rate)

    def observe_summary_miss(self) -> None:
        """摘要遗漏计数（质量 gate 反哺）。"""
        self._summary_miss_total += 1

    def report(self) -> HealthReport:
        """健康度评分（0-100）：
        - 基数 100
        - stuck 信号：每个独立卡点 -35（1 个卡点 → degraded，2 个 → critical）
        - 回捞激增：refetch_rate > 0.5 → -30，> 0.3 → -15
        - 摘要遗漏：每个遗漏 -5（上限 -25）
        - stale 命中：rate > 0.05 → -20（应≈0）
        - 连续恶化检测：最近 3 次回捞单调上升 → -10
        """
        score = 100.0
        stuck_count = self.stuck.stuck_count()
        score -= min(stuck_count * 35, 70)

        refetch = self._recent_avg(self._refetch_history)
        stale = self._recent_avg(self._stale_history)
        if refetch > 0.5:
            score -= 30
        elif refetch > 0.3:
            score -= 15
        if stale > 0.05:
            score -= 20
        score -= min(self._summary_miss_total * 5, 25)

        # 连续恶化：最近 3 个回捞点单调上升
        if len(self._refetch_history) >= 3:
            recent = list(self._refetch_history)[-3:]
            if recent[0] < recent[1] < recent[2]:
                score -= 10

        score = max(0.0, min(100.0, score))
        if score >= 70:
            level, advice = "healthy", "会话正常，继续执行"
        elif score >= 40:
            level, advice = "degraded", "建议：上下文干预（强制压缩）或派 fresh-context 子代理"
        else:
            level, advice = "critical", "建议交接或开新会话（四维指标持续恶化）"

        self._report = HealthReport(
            score=score, stuck_count=stuck_count,
            refetch_rate=refetch, summary_miss=self._summary_miss_total,
            stale_hit_rate=stale, level=level, advice=advice,
        )
        return self._report

    @staticmethod
    def _recent_avg(history: deque) -> float:
        if not history:
            return 0.0
        return sum(history) / len(history)
