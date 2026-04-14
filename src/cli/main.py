"""headless 执行入口：`agent run --task "..."` + `--mode rpc`。

- run：非交互执行，结构化输出到 stdout（自动化/CI/面试 demo 能力）
- --mode rpc：stdin 读 Op（JSONL），stdout 写结构化 Event（对标 pi-web，Web 直接驱动内核）
- 不做交互式 CLI 界面（用户只用 Web）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..agent import AgentLoop, EventIdGenerator
from ..protocol import Op, op_to_json, parse_op
from ..protocol.ops import ApprovalResponse, UserTurnStart
from ..storage import EventLog


class MockLLM:
    """Phase 1 Mock LLM（Phase 5 由 Provider 层替换）。"""

    def generate(self, messages: list[dict], *, role: str = "editor") -> str:
        return "计划：解析任务并执行最小变更。"


class NoopTools:
    """Phase 1 空工具集（Phase 3 由 ToolRegistry 替换）。"""

    def __init__(self) -> None:
        self._tools: list[dict] = []

    def call(self, name: str, args: dict) -> dict:
        return {"status": "ok", "tool": name}

    def list_tools(self) -> list[dict]:
        return self._tools


def default_session_dir() -> Path:
    return Path.home() / ".codemason" / "sessions"


def build_loop(session_id: str, *, event_dir: Path | None = None, mode: str = "act") -> AgentLoop:
    """组装 AgentLoop（Phase 1 用 Mock 组件，后续替换为真实 LLM/Tools）。"""
    session_dir = event_dir or default_session_dir()
    session_dir.mkdir(parents=True, exist_ok=True)
    log = EventLog(session_dir / f"{session_id}.jsonl")
    loop = AgentLoop(
        event_log=log,
        llm=MockLLM(),
        tools=NoopTools(),
        session_id=session_id,
        mode=mode,
        event_id_gen=EventIdGenerator(prefix=session_id),
    )
    return loop


def cmd_run(args: argparse.Namespace) -> int:
    """`agent run --task "..."`：非交互执行一次任务，结构化输出。"""
    session_id = args.session or "auto"
    # mode=rpc 是传输模式，会话模式固定 act（rpc 只影响输出格式）
    session_mode = "act" if args.mode in ("act", "rpc") else args.mode
    loop = build_loop(session_id, mode=session_mode)
    op = UserTurnStart(content=args.task, mode=session_mode)
    loop.enqueue_op(op)
    events = loop.run_until_idle()
    for ev in events:
        print(op_to_json(ev) if args.mode == "rpc" else f"[{ev.type.value}] {ev.model_dump_json(exclude_none=True)}")
    # 输出最终状态
    print(json.dumps({"session": session_id, "state": loop.state_machine.state.value}, ensure_ascii=False))
    return 0


def cmd_rpc(args: argparse.Namespace) -> int:
    """`--mode rpc`：stdin 读 Op（JSONL），stdout 写结构化 Event（对标 pi-web）。"""
    session_id = args.session or "rpc"
    loop = build_loop(session_id, mode="rpc")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            op: Op = parse_op(line)
        except Exception as e:
            print(json.dumps({"error": f"op 解析失败: {e}"}, ensure_ascii=False), flush=True)
            continue
        loop.enqueue_op(op)
        events = loop.run_until_idle(max_steps=50)
        for ev in events:
            print(op_to_json(ev), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description="CodeMason headless 内核")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="非交互执行任务")
    run_p.add_argument("--task", required=True, help="任务描述")
    run_p.add_argument("--session", default=None, help="会话 id")
    run_p.add_argument("--mode", choices=["act", "plan", "rpc"], default="act", help="执行模式")
    run_p.set_defaults(func=cmd_run)

    rpc_p = sub.add_parser("rpc", help="RPC 模式（stdin Op → stdout Event）")
    rpc_p.add_argument("--session", default=None, help="会话 id")
    rpc_p.set_defaults(func=cmd_rpc)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
