"""Loop 调度四件套（G14 v1.22 落地：循环确定性）。

- scheduler.py：Automations 调度触发器（schedule cron / 事件触发 / webhook）
- worktree.py：git worktree 并行隔离（每 agent 一工作树，防 Parallel Collision）
- judge.py：独立 judge 模型族路由（role=judge 验证走不同 Provider，防 Verifier Theater）
- budget.py：loop token 硬预算 + 超限熔断（防 Token Burn）
"""

from .budget import LoopBudget
from .inbox import ApprovalInbox, InboxItem
from .judge import JudgeRouter
from .scheduler import LoopScheduler, ScheduleRule, TriggerResult
from .worktree import Worktree, WorktreeManager

__all__ = [
    "LoopScheduler",
    "ScheduleRule",
    "TriggerResult",
    "WorktreeManager",
    "Worktree",
    "JudgeRouter",
    "LoopBudget",
    "ApprovalInbox",
    "InboxItem",
]
