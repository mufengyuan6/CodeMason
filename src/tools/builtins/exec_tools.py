"""内置工具：Bash / Monitor / WebSearch / WebFetch / AskUserQuestion。"""

from __future__ import annotations

import time
from typing import Optional

from ...security.exec_sandbox import SandboxFactory, SandboxProvider
from ...security.guard import ShellGuard
from ..base import Tool, ToolContext
from ..registry import register_tool

# 工具层最后一道防线（P2 修复：原 DANGEROUS_PATTERNS 死代码从未被检查——
# 黑名单实际由 security/guard.py 的 ShellGuard 承担，但仅在 plan_act 层调用。
# 现在 Bash/Monitor 执行前也过 ShellGuard，纵深防御：即使审批层被绕过，工具层仍拦截)。
_GUARD = ShellGuard()

# G19 执行沙箱（v1.23 落地）：默认工厂按可用性探测自动选层（L3 Firecracker 优先）。
# 本机无 Docker/FC/E2B → 自动降级受限 local 后端；企业换真环境零改动（换层只换实现）。
_SANDBOX: Optional[SandboxProvider] = None


def set_sandbox(provider: Optional[SandboxProvider]) -> None:
    """注入沙箱后端（测试/企业部署用）。None 恢复工厂自动选层。"""
    global _SANDBOX
    _SANDBOX = provider


def _get_sandbox() -> SandboxProvider:
    global _SANDBOX
    if _SANDBOX is None:
        _SANDBOX = SandboxFactory().create()
    return _SANDBOX


class BashTool(Tool):
    name = "Bash"
    description = "执行 shell 命令（执行类，高危需审批；经执行沙箱隔离）"
    parameters = {"command": {"type": "string", "description": "shell 命令"}, "timeout": {"type": "integer", "description": "超时秒数，默认 30"}}

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict:
        command = args["command"]
        timeout = int(args.get("timeout", 30))
        # 工具层黑名单硬拦截（确定性，不依赖模型/审批层）
        guard = _GUARD.check(command)
        if guard["blocked"]:
            return {"status": "blocked", "reason": f"ShellGuard 拦截: {guard['reason']}"}
        # G19 执行沙箱（v1.23 落地）：替代裸 subprocess.run(shell=True)
        sandbox = _get_sandbox()
        result = sandbox.run(command, cwd=(context.cwd if context else "."), timeout=timeout)
        return {
            "status": "ok" if result.exit_code == 0 else ("error" if result.exit_code is None else "ok"),
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "executor": result.executor,
            "sandbox_id": result.sandbox_id,
            "timed_out": result.timed_out,
        }


class MonitorTool(Tool):
    name = "Monitor"
    description = "监控命令/进程输出（周期采样；经执行沙箱隔离）"
    parameters = {"command": {"type": "string", "description": "监控命令"}, "interval": {"type": "integer", "description": "采样间隔秒"}}

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict:
        command = args["command"]
        interval = float(args.get("interval", 1.0))
        samples = []
        # 工具层黑名单硬拦截（同上）
        guard = _GUARD.check(command)
        if guard["blocked"]:
            return {"status": "blocked", "reason": f"ShellGuard 拦截: {guard['reason']}"}
        try:
            sandbox = _get_sandbox()
            result = sandbox.run(command, cwd=(context.cwd if context else "."), timeout=interval * 2 + 1)
            if result.stdout:
                samples = [line for line in result.stdout.splitlines() if line.strip()]
            return {"status": "ok", "samples": samples[-100:], "count": len(samples), "executor": result.executor}
        except Exception as e:
            return {"status": "error", "error": str(e)}


class WebSearchTool(Tool):
    name = "WebSearch"
    description = "网络搜索（只读；未配置搜索后端时返回提示）"
    parameters = {"query": {"type": "string", "description": "搜索关键词"}}

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict:
        # 占位实现：真实搜索后端可插拔（T5/T6 接 WebSearch provider）
        return {"status": "ok", "results": [], "note": "搜索后端未配置，返回空结果"}


class WebFetchTool(Tool):
    name = "WebFetch"
    description = "抓取 URL 内容（只读；未配置网络后端时返回提示）"
    parameters = {"url": {"type": "string", "description": "目标 URL"}}

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict:
        return {"status": "ok", "content": "", "note": "网络后端未配置，返回空内容"}


class AskUserTool(Tool):
    name = "AskUser"
    description = "向用户提问（交互类，需澄清时使用）"
    parameters = {"question": {"type": "string", "description": "问题"}, "options": {"type": "array", "description": "选项列表"}}

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict:
        # 返回 ASK_USER 标记，由 Loop 转 ASKING_USER 状态
        return {"status": "ask_user", "question": args.get("question", ""), "options": args.get("options", [])}


register_tool(BashTool())
register_tool(MonitorTool())
register_tool(WebSearchTool())
register_tool(WebFetchTool())
register_tool(AskUserTool())
