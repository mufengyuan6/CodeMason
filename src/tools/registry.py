"""工具注册表。

- 只读：Read / Glob / Grep / WebSearch / WebFetch
- 写入：Write / Edit（diff 级）
- 执行：Bash / Monitor
- 交互：AskUserQuestion
- 10 工具全通 + 审批（Phase 2 验收标准）
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Callable, Optional

from .base import Tool, ToolContext


class ToolRegistry:
    """工具注册表：注册、发现、调用。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def list_tools(self) -> list[dict]:
        """供 Agent Loop 消费的工具清单（ToolProtocol 接口）。"""
        return [{"name": t.name, "args": t.parameters} for t in self._tools.values()]

    def call(self, name: str, args: dict, context: Optional[ToolContext] = None) -> dict:
        tool = self.get(name)
        if tool is None:
            return {"status": "error", "error": f"未知工具: {name}"}
        return tool.run(args or {}, context)

    def auto_discover(self, package: str = "src.tools.builtins") -> None:
        """自动发现并注册 builtins 包内所有工具（模块级 register_tool 调用）。"""
        pkg = importlib.import_module(package)
        for mod_info in pkgutil.iter_modules(pkg.__path__):
            importlib.import_module(f"{package}.{mod_info.name}")


# 模块级注册机制：工具模块内调用 register_tool(ToolInstance) 完成自动注册
_registry: Optional[ToolRegistry] = None


def set_registry(registry: ToolRegistry) -> None:
    global _registry
    _registry = registry


def register_tool(tool: Tool) -> None:
    """模块级注册函数：builtins/*.py 模块顶部调用。"""
    if _registry is not None:
        _registry.register(tool)
