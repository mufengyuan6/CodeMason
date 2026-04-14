"""反思节点。

错误分类体系（error_classification）：语法 / 权限 / 路径 / 逻辑 / 网络 / 其他
策略选择：重试 / 换工具 / 换方案 / 问用户
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class ErrorClass(str, Enum):
    SYNTAX = "syntax"          # 语法错误
    PERMISSION = "permission"  # 权限错误
    PATH = "path"              # 路径错误
    LOGIC = "logic"            # 逻辑错误
    NETWORK = "network"        # 网络错误
    RESOURCE = "resource"      # 资源不足
    OTHER = "other"            # 其他


class Strategy(str, Enum):
    RETRY = "retry"            # 重试（瞬时错误）
    CHANGE_TOOL = "change_tool"  # 换工具
    CHANGE_PLAN = "change_plan"  # 换方案
    ASK_USER = "ask_user"      # 问用户


# 错误特征 → 错误分类（启发式规则）
ERROR_CLASSIFIERS: list[tuple[list[str], ErrorClass]] = [
    (["SyntaxError", "syntax error", "ParseError", "IndentationError"], ErrorClass.SYNTAX),
    (["PermissionError", "permission denied", "EACCES", "Not authorized", "403"], ErrorClass.PERMISSION),
    (["FileNotFoundError", "No such file", "ENOENT", "not found", "No such directory"], ErrorClass.PATH),
    (["TypeError", "ValueError", "KeyError", "IndexError", "AttributeError", "AssertionError"], ErrorClass.LOGIC),
    (["ConnectionError", "timeout", "TimeoutError", "ECONNREFUSED", "network", "SocketError"], ErrorClass.NETWORK),
    (["MemoryError", "out of memory", "ENOMEM", "Disk quota"], ErrorClass.RESOURCE),
]


class ReflectionEngine:
    """反思归因：错误文本 → 分类 → 策略选择。"""

    def classify(self, error_text: str) -> ErrorClass:
        """错误分类（启发式特征匹配）。"""
        for keywords, cls in ERROR_CLASSIFIERS:
            if any(k in error_text for k in keywords):
                return cls
        return ErrorClass.OTHER

    def choose_strategy(self, error_class: ErrorClass, retry_count: int = 0) -> Strategy:
        """策略选择：分类 + 重试次数 → 策略。"""
        if error_class in (ErrorClass.NETWORK, ErrorClass.RESOURCE):
            return Strategy.RETRY if retry_count < 2 else Strategy.ASK_USER
        if error_class == ErrorClass.PATH:
            return Strategy.CHANGE_TOOL
        if error_class == ErrorClass.PERMISSION:
            return Strategy.ASK_USER
        if error_class == ErrorClass.SYNTAX:
            return Strategy.RETRY if retry_count < 1 else Strategy.CHANGE_PLAN
        return Strategy.CHANGE_PLAN

    def reflect(self, error_text: str, retry_count: int = 0) -> dict:
        """完整反思：归因 + 策略 + 建议。"""
        cls = self.classify(error_text)
        strategy = self.choose_strategy(cls, retry_count)
        return {
            "error_class": cls.value,
            "strategy": strategy.value,
            "retry_count": retry_count,
            "error_snippet": error_text[:200],
        }
