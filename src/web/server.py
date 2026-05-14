"""CodeMason 驾驶舱启动入口。

用法：
    python -m src.web.server [--port 8765] [--token xxx] [--frontend ../frontend/dist]

- 初始化驾驶舱（鉴权 token / 事件存储 / Agent Loop）
- 启动 uvicorn（默认只绑 127.0.0.1，G5 安全基线）
- 挂载前端构建产物（若存在）
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

import uvicorn

from . import main as web_main
from .main import init_cockpit, mount_frontend


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cockpit", description="CodeMason 驾驶舱")
    parser.add_argument("--port", type=int, default=48408, help="固定端口（local-port-manager 分配）")
    parser.add_argument("--host", default="127.0.0.1", help="默认只绑 127.0.0.1（G5）")
    parser.add_argument("--token", default=None, help="鉴权 token（默认随机生成）")
    parser.add_argument("--session", default="web")
    parser.add_argument("--frontend", default=None, help="前端构建产物目录（默认 ../frontend/dist）")
    parser.add_argument("--reload", action="store_true", help="开发热重载")
    args = parser.parse_args(argv)

    token = args.token or secrets.token_hex(8)
    init_cockpit(session_id=args.session, token=token)

    frontend_dir = args.frontend
    if frontend_dir is None:
        frontend_dir = str(Path(__file__).resolve().parent.parent.parent / "frontend" / "dist")
    mount_frontend(frontend_dir)

    print("=" * 50)
    print(f"  CodeMason 驾驶舱已启动")
    print(f"  地址: http://{args.host}:{args.port}")
    print(f"  Token: {token}")
    print(f"  会话: {args.session}  事件: {web_main.EVENT_LOG.path}")
    print("  注意: 默认只绑 127.0.0.1，Web 可审批命令（攻击面最小化 G5）")
    print("=" * 50)

    uvicorn.run("src.web.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
