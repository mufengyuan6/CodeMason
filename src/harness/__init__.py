"""Harness 层：统一 Hook 框架（YAGNI + 权限共用）+ 旧规则引擎（兼容）。"""

from .hook_framework import (
    BaseHook,
    HookContext,
    HookEvent,
    HookPriority,
    HookResult,
    HooksManager as UnifiedHooksManager,
    YagniValidationHook,
)

__all__ = [
    "BaseHook",
    "HookContext",
    "HookEvent",
    "HookPriority",
    "HookResult",
    "UnifiedHooksManager",
    "YagniValidationHook",
]

# 兼容旧 API（旧 web/main.py 使用，T6 重写后移除）
from .hooks import HooksManager, DangerousOperationHook, PermissionCheckHook  # noqa: F401
from .rules_engine import RulesEngine, YAGNI_DecisionEngine, YAGNI_Level  # noqa: F401

__all__ += [
    "RulesEngine",
    "YAGNI_DecisionEngine",
    "YAGNI_Level",
    "HooksManager",
    "DangerousOperationHook",
    "PermissionCheckHook",
]
