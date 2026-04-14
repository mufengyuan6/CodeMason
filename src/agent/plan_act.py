"""Plan/Act 双模式。

三保险：
1. prompt 层：系统提示声明"Plan 模式只读"
2. 工具预设禁用编辑：Plan 模式禁止 Write/Edit/Bash
3. shell 命令黑名单硬拦截：即使绕过工具层，ShellGuard 拦截写操作

模式切换：销毁旧 session 用同 sessionId 重建（上下文传递）。
"""

from __future__ import annotations

from typing import Optional

from ..security.guard import ShellGuard
from ..security.policy import EXEC_TOOLS, WRITE_TOOLS


class PlanActCoordinator:
    """Plan/Act 模式协调器：模式状态 + 只读拦截 + 切换。"""

    # Plan 模式禁用的工具（写入+执行）
    PLAN_FORBIDDEN_TOOLS = WRITE_TOOLS | EXEC_TOOLS

    def __init__(self, mode: str = "act") -> None:
        self.mode = mode  # act / plan
        self.guard = ShellGuard()

    def switch(self, mode: str) -> dict:
        """切换模式（Plan ↔ Act）。"""
        if mode not in ("act", "plan"):
            raise ValueError(f"未知模式: {mode}")
        self.mode = mode
        return {"mode": mode, "readonly": mode == "plan"}

    @property
    def readonly(self) -> bool:
        return self.mode == "plan"

    def check_tool(self, tool_name: str) -> Optional[str]:
        """工具预设检查：Plan 模式拦截写入/执行类工具。返回拦截原因或 None。"""
        if self.mode == "plan" and tool_name in self.PLAN_FORBIDDEN_TOOLS:
            return f"Plan 模式只读：禁止调用 {tool_name}"
        return None

    def check_command(self, command: str) -> Optional[str]:
        """shell 黑名单硬拦截（第三保险）：Plan 模式禁止任何写命令。"""
        if self.mode != "plan":
            return None
        # Plan 模式下所有 Bash 调用都拦截（即使命令本身安全，Plan 不执行）
        return "Plan 模式只读：禁止执行 shell 命令"

    def check_edit_args(self, args: dict) -> Optional[str]:
        """写入参数检查：Plan 模式禁止 Write/Edit 的写入参数。"""
        if self.mode != "plan":
            return None
        if args.get("content") is not None or args.get("new_string") is not None:
            return "Plan 模式只读：禁止修改文件内容"
        return None

    def describe(self) -> dict:
        return {"mode": self.mode, "readonly": self.readonly, "forbidden_tools": sorted(self.PLAN_FORBIDDEN_TOOLS)}
