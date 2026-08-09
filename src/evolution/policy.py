"""EvolutionPolicy 策略层（v1.31，G22）——控制进化边界，防过度进化。

对应 JD 要求的"可插拔验证 Harness"：
- 速率限制：单次进化最多改 N 个条目
- 效果监控：进化后 7 天内持续监控指标
- 回滚机制：进化失败自动回退到上一版
- 冷却期：同一作用目标 N 小时内不重复进化
- 人类审批：高风险进化（Harness 改动）走审批收件箱
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PolicyConfig:
    """策略配置。"""

    max_items_per_cycle: int = 5
    """单次进化最多改 N 个条目"""

    effect_monitoring_window_days: int = 7
    """效果监控窗口（天）"""

    cooldown_hours: float = 4.0
    """同一作用目标冷却期（小时）"""

    require_approval_targets: list[str] = field(default_factory=lambda: ["harness"])
    """需要人类审批的作用目标"""

    auto_rollback_on_regression: bool = True
    """回归时自动回滚"""

    regression_threshold: float = -0.05
    """回归判定阈值（指标下降超过此值触发回滚）"""

    max_cycles_per_hour: int = 6
    """每小时最大进化次数"""

    min_confidence: float = 0.3
    """候选最低置信度"""


@dataclass
class CooldownEntry:
    """冷却期记录。"""

    target: str
    last_cycle_time: float
    next_available: float


@dataclass
class PolicyDecision:
    """策略判定结果。"""

    allowed: bool = True
    reason: str = ""
    requires_approval: bool = False
    truncated_count: int = 0  # 被速率限制截断的候选数


class EvolutionPolicy:
    """进化策略控制（v1.31，G22）。

    防过度进化的策略层——对应 JD"可插拔验证 Harness"。

    用法::

        policy = EvolutionPolicy()
        decision = policy.check(candidates, target="memory")
        if decision.allowed:
            # 执行进化
            pass
        if decision.requires_approval:
            # 进审批收件箱
            pass
    """

    def __init__(self, config: Optional[PolicyConfig] = None) -> None:
        self._config = config or PolicyConfig()
        self._cooldowns: Dict[str, CooldownEntry] = {}
        self._cycle_timestamps: list[float] = []
        self._rollback_log: list[dict] = []

    @property
    def config(self) -> PolicyConfig:
        return self._config

    def check(self, candidates: list, target: str = "",
              session_id: str = "") -> PolicyDecision:
        """检查候选是否符合策略约束。

        Args:
            candidates: 候选列表（需有 confidence 属性或字典）
            target: 作用目标
            session_id: 会话 ID

        Returns:
            PolicyDecision 判定结果
        """
        now = time.time()
        decision = PolicyDecision()

        # 1. 速率限制：每小时最大进化次数
        self._cycle_timestamps = [
            t for t in self._cycle_timestamps if now - t < 3600
        ]
        if len(self._cycle_timestamps) >= self._config.max_cycles_per_hour:
            decision.allowed = False
            decision.reason = f"速率限制：已达到每小时 {self._config.max_cycles_per_hour} 次上限"
            return decision

        # 2. 冷却期检查
        if target and target in self._cooldowns:
            entry = self._cooldowns[target]
            if now < entry.next_available:
                remaining = entry.next_available - now
                decision.allowed = False
                decision.reason = f"冷却期：{target} 还需等待 {remaining/3600:.1f} 小时"
                return decision

        # 3. 速率限制：单次最多 N 个条目
        if len(candidates) > self._config.max_items_per_cycle:
            decision.truncated_count = len(candidates) - self._config.max_items_per_cycle
            candidates = candidates[:self._config.max_items_per_cycle]

        # 4. 置信度过滤
        filtered = []
        for c in candidates:
            conf = getattr(c, "confidence", 0.0) if hasattr(c, "confidence") else (c.get("confidence", 0.0) if isinstance(c, dict) else 0.0)
            if conf >= self._config.min_confidence:
                filtered.append(c)
        if len(filtered) < len(candidates):
            decision.truncated_count += len(candidates) - len(filtered)

        # 5. 人类审批检查
        if target in self._config.require_approval_targets:
            decision.requires_approval = True
            decision.reason = f"{target} 需要人类审批"

        # 6. 记录冷却期
        if target and decision.allowed:
            self._cooldowns[target] = CooldownEntry(
                target=target,
                last_cycle_time=now,
                next_available=now + self._config.cooldown_hours * 3600,
            )

        # 7. 记录周期时间戳
        if decision.allowed:
            self._cycle_timestamps.append(now)

        return decision

    def check_regression(self, validation: Any,
                         candidate: Any = None) -> dict:
        """检查验证结果是否触发回滚。

        Returns:
            {"should_rollback": bool, "reason": str}
        """
        delta = getattr(validation, "regression_delta", 0.0) if hasattr(validation, "regression_delta") else (validation.get("regression_delta", 0.0) if isinstance(validation, dict) else 0.0)

        if not self._config.auto_rollback_on_regression:
            return {"should_rollback": False, "reason": "auto_rollback 已禁用"}

        if delta < self._config.regression_threshold:
            return {
                "should_rollback": True,
                "reason": f"回归判定：delta={delta:.3f} < 阈值 {self._config.regression_threshold}",
            }

        return {"should_rollback": False, "reason": "验证通过"}

    def record_rollback(self, cycle_id: str, candidate_id: str,
                        reason: str) -> None:
        """记录回滚事件。"""
        self._rollback_log.append({
            "cycle_id": cycle_id,
            "candidate_id": candidate_id,
            "reason": reason,
            "timestamp": time.time(),
        })

    def get_rollback_history(self, limit: int = 10) -> list[dict]:
        """获取回滚历史。"""
        return self._rollback_log[-limit:]

    def is_cooled_down(self, target: str) -> bool:
        """检查目标是否已冷却完毕。"""
        if target not in self._cooldowns:
            return True
        return time.time() >= self._cooldowns[target].next_available

    def get_next_available_time(self, target: str) -> Optional[float]:
        """获取目标下次可用时间。"""
        if target not in self._cooldowns:
            return None
        entry = self._cooldowns[target]
        if time.time() >= entry.next_available:
            return None
        return entry.next_available
