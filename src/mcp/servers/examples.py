"""MCP 示例 Server。

3 个示例：GitHub / 数据库 / 云服务（M×N 问题：M+N 个标准化实现）。
示例 Server 用 stdio JSON-RPC 2.0 实现最小工具集，证明协议级扩展通用性。
"""

from __future__ import annotations

import json
import sys
from typing import Callable


class SampleMcpServer:
    """最小 MCP Server 骨架（stdio 协议）：注册工具 + 循环处理请求。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self._tools: dict[str, Callable[[dict], dict]] = {}
        self._request_id = 0

    def tool(self, name: str, description: str, schema: dict):
        """装饰器：注册工具。"""

        def decorator(fn):
            self._tools[name] = fn
            return fn

        return decorator

    def run(self) -> None:
        """stdio 事件循环。"""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            method = msg.get("method")
            if method == "initialize":
                self._send(msg.get("id"), {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": self.name, "version": "1.0"}})
            elif method == "notifications/initialized":
                pass
            elif method == "tools/list":
                tools = [{"name": n, "description": d, "inputSchema": s} for n, d, s in self._tool_metadata()]
                self._send(msg.get("id"), {"tools": tools})
            elif method == "tools/call":
                params = msg.get("params", {})
                name = params.get("name", "")
                args = params.get("arguments", {})
                fn = self._tools.get(name)
                if fn is None:
                    self._send(msg.get("id"), {"content": [{"type": "text", "text": f"未知工具 {name}"}], "isError": True})
                else:
                    try:
                        result = fn(args)
                        self._send(msg.get("id"), {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False})
                    except Exception as e:
                        self._send(msg.get("id"), {"content": [{"type": "text", "text": str(e)}], "isError": True})

    def _tool_metadata(self) -> list[tuple]:
        # 从注册的 fn 取 __doc__ 与 __annotations__ 简化为 schema
        meta = []
        for name, fn in self._tools.items():
            doc = (fn.__doc__ or "").strip()
            meta.append((name, doc, {"type": "object"}))
        return meta

    def _send(self, req_id, result) -> None:
        payload = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


def main_github() -> None:
    """GitHub 示例 Server：list_repos / get_repo_info（mock 数据，证明协议通用性）。"""
    server = SampleMcpServer("github")

    @server.tool("list_repos", "列出用户仓库", {"type": "object"})
    def list_repos(args: dict) -> dict:
        return {"repos": ["codemason", "ai-tools", "web-scraper"]}

    @server.tool("get_repo_info", "获取仓库信息", {"type": "object"})
    def get_repo_info(args: dict) -> dict:
        repo = args.get("repo", "")
        return {"repo": repo, "stars": 128, "language": "Python"}

    server.run()


def main_db() -> None:
    """数据库示例 Server：query（mock SQL 执行器）。"""
    server = SampleMcpServer("database")

    @server.tool("query", "执行 SQL 查询", {"type": "object"})
    def query(args: dict) -> dict:
        sql = args.get("sql", "")
        return {"rows": [{"sql": sql, "result": "ok"}], "row_count": 1}

    server.run()


def main_cloud() -> None:
    """云服务示例 Server：list_buckets / upload_object。"""
    server = SampleMcpServer("cloud")

    @server.tool("list_buckets", "列出对象存储桶", {"type": "object"})
    def list_buckets(args: dict) -> dict:
        return {"buckets": ["assets", "backups", "logs"]}

    @server.tool("upload_object", "上传对象", {"type": "object"})
    def upload_object(args: dict) -> dict:
        return {"bucket": args.get("bucket", ""), "key": args.get("key", ""), "status": "uploaded"}

    server.run()


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "github"
    {"github": main_github, "db": main_db, "cloud": main_cloud}[which]()
