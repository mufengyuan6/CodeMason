"""三级权限策略。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# 工具 → 权限类别
READ_TOOLS = {"Read", "Glob", "Grep", "WebSearch", "WebFetch", "Monitor"}
WRITE_TOOLS = {"Write", "Edit"}
EXEC_TOOLS = {"Bash"}
INTERACT_TOOLS = {"AskUser"}


@dataclass
class PolicyDecision:
    """权限决策结果。"""

    tool_name: str
    category: str  # read / write / exec / interact
    risk_level: str  # red / yellow / green
    needs_approval: bool
    reason: str = ""


class SecurityPolicy:
    """三级权限策略：工具分类 + 风险定级 + 审批判断。"""

    def __init__(self) -> None:
        self.guard = None  # 由调用方注入 ShellGuard（避免循环依赖）

    def evaluate(self, tool_name: str, args: dict, autonomy_level: int = 2) -> PolicyDecision:
        """评估一次工具调用。"""
        if tool_name in READ_TOOLS:
            return PolicyDecision(tool_name, "read", "green", False, "只读默认放行")
        if tool_name in INTERACT_TOOLS:
            return PolicyDecision(tool_name, "interact", "green", False, "交互工具")
        if tool_name in WRITE_TOOLS:
            return PolicyDecision(tool_name, "write", "yellow", True, "写入需展示 diff 审批")
        if tool_name in EXEC_TOOLS:
            command = str(args.get("command", ""))
            risk = "green"
            if self.guard is not None:
                result = self.guard.check(command)
                if result["blocked"]:
                    return PolicyDecision(tool_name, "exec", "red", True, f"黑名单拦截: {result['reason']}")
                risk = result["risk_level"]
            needs = risk == "red" or (risk == "yellow" and autonomy_level <= 2)
            return PolicyDecision(tool_name, "exec", risk, needs, "执行命令按风险定级")
        return PolicyDecision(tool_name, "unknown", "red", True, f"未知工具 {tool_name}")
