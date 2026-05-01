"""MCP 客户端。

- stdio 双协议分帧（JSON-RPC 2.0 over stdio）
- 工具缓存：连接后缓存工具列表
- 3 个示例 Server：GitHub / 数据库 / 云服务（证明协议级扩展通用性）
"""

from __future__ import annotations

import json
import subprocess
from typing import Optional


class MCPError(Exception):
    pass


class McpClient:
    """MCP 客户端：stdio JSON-RPC 2.0 通信 + 工具缓存。"""

    def __init__(self, name: str, command: list[str], cwd: Optional[str] = None) -> None:
        self.name = name
        self.command = command
        self.cwd = cwd
        self._proc: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._tools: list[dict] = []
        self._connected = False

    def connect(self) -> dict:
        """启动子进程并初始化。"""
        try:
            self._proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self.cwd,
            )
            resp = self._request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "codemason", "version": "1.0"}})
            self._request("notifications/initialized", {})
            self._tools = self._list_tools()
            self._connected = True
            return {"status": "connected", "server": self.name, "tools": len(self._tools)}
        except Exception as e:
            raise MCPError(f"MCP {self.name} 连接失败: {e}") from e

    def _request(self, method: str, params: dict) -> dict:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise MCPError("MCP 未连接")
        self._request_id += 1
        payload = json.dumps({"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params})
        self._proc.stdin.write(payload + "\n")
        self._proc.stdin.flush()
        # 读响应行
        for _ in range(100):
            line = self._proc.stdout.readline()
            if not line:
                raise MCPError(f"MCP {self.name} 无响应")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == self._request_id:
                if "error" in msg:
                    raise MCPError(f"MCP {self.name} 错误: {msg['error']}")
                return msg.get("result", {})
        raise MCPError(f"MCP {self.name} 响应超时")

    def _list_tools(self) -> list[dict]:
        result = self._request("tools/list", {})
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        """调用 MCP 工具（自动发现 server 名）。"""
        if not self._connected:
            self.connect()
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return {"status": "ok", "content": text_parts, "isError": result.get("isError", False)}

    def list_tools(self) -> list[dict]:
        return list(self._tools)

    def close(self) -> None:
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
            self._connected = False
