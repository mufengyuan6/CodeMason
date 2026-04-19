"""内置工具：Bash / Monitor / WebSearch / WebFetch / AskUserQuestion。"""

from __future__ import annotations

import shlex
import subprocess
import time
from typing import Optional

from ..base import Tool, ToolContext
from ..registry import register_tool

# 危险命令黑名单
DANGEROUS_PATTERNS = ("rm -rf", "rm -fr", "sudo ", "mkfs", "dd if=", ":(){", "> /dev/sda", "chmod -R 777", "git push -f")


class BashTool(Tool):
    name = "Bash"
    description = "执行 shell 命令（执行类，高危需审批）"
    parameters = {"command": {"type": "string", "description": "shell 命令"}, "timeout": {"type": "integer", "description": "超时秒数，默认 30"}}

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict:
        command = args["command"]
        timeout = int(args.get("timeout", 30))
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=(context.cwd if context else "."),
            )
            return {
                "status": "ok",
                "exit_code": result.returncode,
                "stdout": result.stdout[-5000:],
                "stderr": result.stderr[-2000:],
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": f"命令超时（{timeout}s）"}
        except Exception as e:
            return {"status": "error", "error": str(e)}


class MonitorTool(Tool):
    name = "Monitor"
    description = "监控命令/进程输出（周期采样）"
    parameters = {"command": {"type": "string", "description": "监控命令"}, "interval": {"type": "integer", "description": "采样间隔秒"}}

    def run(self, args: dict, context: Optional[ToolContext] = None) -> dict:
        command = args["command"]
        interval = float(args.get("interval", 1.0))
        samples = []
        try:
            proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=(context.cwd if context else "."))
            deadline = time.time() + float(args.get("duration", 5))
            while time.time() < deadline and proc.poll() is None:
                line = proc.stdout.readline() if proc.stdout else ""
                if line:
                    samples.append(line.strip())
                time.sleep(interval)
            proc.kill()
            return {"status": "ok", "samples": samples[-100:], "count": len(samples)}
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
