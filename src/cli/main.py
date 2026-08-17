"""headless 执行入口：`agent run --task "..."` + `--mode rpc`。

- run：非交互执行，结构化输出到 stdout（自动化/CI/面试 demo 能力）
- --mode rpc：stdin 读 Op（JSONL），stdout 写结构化 Event（对标 pi-web，Web 直接驱动内核）
- 不做交互式 CLI 界面（用户只用 Web）

v1.30（T-11b）：默认接入真实 LLM（deepseek-v4-flash architect/editor）
+ 真实工具集（12 工具 auto_discover）；--mock 开关保留 Mock 降级（离线/测试）。
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
    """Phase 1 Mock LLM（--mock 模式）。"""

    def generate(self, messages: list[dict], *, role: str = "editor") -> str:
        return "计划：解析任务并执行最小变更。"


class NoopTools:
    """Phase 1 空工具集（--mock 模式）。"""

    def __init__(self) -> None:
        self._tools: list[dict] = []

    def call(self, name: str, args: dict) -> dict:
        return {"status": "ok", "tool": name}

    def list_tools(self) -> list[dict]:
        return self._tools


class CLIExecutionTools:
    """ToolRegistry → AgentLoop 适配器（v1.30 T-11b）。

    AgentLoop._do_waiting_tool 的 Phase 1 行为：list_tools()[0] → call(name, args)。
    list_tools 必须返回 mock 调用（空 args）——不能返回 schema（否则 args 是 dict，
    pydantic 校验报错）；call 走真实 ToolRegistry（工具结果进事件流，Phase 1 下游消费）。
    """

    def __init__(self, registry: object) -> None:
        self._registry = registry

    def list_tools(self) -> list[dict]:
        """返回 mock 工具调用（空 args）——AgentLoop 消费 list_tools()[0] 的 name 字段。"""
        real_tools = self._registry.list_tools()
        return [{"name": t["name"], "args": {}} for t in real_tools]

    def call(self, name: str, args: dict) -> dict:
        """调用真实工具——走 ToolRegistry.call，结果进事件流。"""
        try:
            return self._registry.call(name, args)
        except Exception as e:
            return {"status": "error", "error": str(e)[:200]}


def default_session_dir() -> Path:
    return Path.home() / ".codemason" / "sessions"


def build_loop(session_id: str, *, event_dir: Path | None = None, mode: str = "act", mock: bool = False) -> AgentLoop:
    """组装 AgentLoop。

    v1.30（T-11b）：mock=False（默认）走真实 LLM（deepseek-v4-flash）+ 12 工具；
    mock=True 走 MockLLM+NoopTools（离线/测试安全）。
    """
    session_dir = event_dir or default_session_dir()
    session_dir.mkdir(parents=True, exist_ok=True)
    log = EventLog(session_dir / f"{session_id}.jsonl")

    llm = MockLLM()
    tools = NoopTools()

    if not mock:
        try:
            from ..providers.adapter import build_adapter_from_credentials

            llm = build_adapter_from_credentials()
            from ..tools.registry import ToolRegistry, set_registry

            registry = ToolRegistry()
            set_registry(registry)
            registry.auto_discover()
            tools = CLIExecutionTools(registry)  # v1.30 T-11b：适配器包裹
        except Exception as e:
            print(f"[CLI] 真实 LLM/工具初始化失败，降级 Mock: {e}", file=sys.stderr)

    loop = AgentLoop(
        event_log=log,
        llm=llm,
        tools=tools,
        session_id=session_id,
        mode=mode,
        event_id_gen=EventIdGenerator(prefix=session_id),
    )
    return loop


def cmd_run(args: argparse.Namespace) -> int:
    """`agent run --task "..."`：非交互执行一次任务，结构化输出。"""
    session_id = args.session or "auto"
    session_mode = "act" if args.mode in ("act", "rpc") else args.mode
    loop = build_loop(session_id, mode=session_mode, mock=args.mock)
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
    loop = build_loop(session_id, mode="rpc", mock=args.mock)
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
    run_p.add_argument("--mock", action="store_true", help="使用 Mock LLM/工具（离线/测试模式）")
    run_p.set_defaults(func=cmd_run)

    rpc_p = sub.add_parser("rpc", help="RPC 模式（stdin Op → stdout Event）")
    rpc_p.add_argument("--session", default=None, help="会话 id")
    rpc_p.add_argument("--mock", action="store_true", help="使用 Mock LLM/工具（离线/测试模式）")
    rpc_p.set_defaults(func=cmd_rpc)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
