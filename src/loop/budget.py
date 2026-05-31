"""loop token 硬预算（G14 v1.22 落地：防 Token Burn）。

设计（design.md G14）：
- 每 loop 声明 token 硬上限 + 超限熔断（复用 4.1 路由合规审计 + 5.1 成本驾驶舱数据底座）
- Uber 4 个月烧完全年 Claude Code 预算、单周末 $4200 的行业教训
- 与 CostLedger 联动（成本驾驶舱实时可见）

范式声明：业务逻辑层 OOP。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BudgetState:
    """loop 预算状态。"""

    budget_id: str
    hard_limit: int  # token 硬上限
    used_tokens: int = 0
    tripped: bool = False
    started_at: float = field(default_factory=time.time)
    tripped_at: Optional[float] = None


class LoopBudget:
    """loop token 硬预算：每 Op 记账 + 超限熔断（fail-closed）。

    用法：
        budget = LoopBudget(hard_limit=100_000)
        budget.record(1200)
        if budget.exceeded():  # 超限 → 熔断（不再发新 Op）
            ...
    """

    def __init__(self, hard_limit: int, budget_id: str = "loop-1") -> None:
        self.hard_limit = hard_limit
        self.state = BudgetState(budget_id=budget_id, hard_limit=hard_limit)
        self._calls: list[tuple[float, int]] = []

    def record(self, tokens: int) -> dict:
        """记录一次消耗。返回预算快照。"""
        self.state.used_tokens += tokens
        self._calls.append((time.time(), tokens))
        if self.state.used_tokens > self.hard_limit and not self.state.tripped:
            self.state.tripped = True
            self.state.tripped_at = time.time()
        return self.snapshot()

    def exceeded(self) -> bool:
        """是否超限（熔断信号）。"""
        return self.state.tripped

    def remaining(self) -> int:
        return max(self.hard_limit - self.state.used_tokens, 0)

    def snapshot(self) -> dict:
        return {
            "budget_id": self.state.budget_id,
            "hard_limit": self.hard_limit,
            "used_tokens": self.state.used_tokens,
            "remaining": self.remaining(),
            "tripped": self.state.tripped,
            "usage_ratio": round(self.state.used_tokens / max(self.hard_limit, 1), 3),
        }

    def reset(self) -> None:
        """重置（新 loop 轮次）。"""
        self.state = BudgetState(budget_id=self.state.budget_id, hard_limit=self.hard_limit)
        self._calls = []
