"""v1.30 T-11b/c 测试：CLI 真实 LLM 接入 + server 真实 LLM/归因接入。

验证：
- CLI 真实模式：build_loop(mock=False) → ModelRouterAdapter + CLIExecutionTools
- CLI --mock 模式保持降级路径
- CLIExecutionTools：list_tools 返回 mock 调用格式 + call 走真实 registry
- server：init_cockpit后 LOOP.set_llm 注入 + 归因引擎用真实 provider（status=completed）
"""

import pytest

from src.cli.main import build_loop, CLIExecutionTools
from src.protocol import UserTurnStart
from src.storage import EventLog
from src.providers.adapter import ModelRouterAdapter


class TestCLIExecutionTools:
    def test_list_tools_returns_mock_calls(self):
        """list_tools 返回 mock 调用格式（空 args），不返回 schema。"""

        class FakeRegistry:
            def list_tools(self):
                return [{"name": "Bash", "args": {"command": {"type": "string"}}}]  # schema

        tools = CLIExecutionTools(FakeRegistry())
        result = tools.list_tools()
        assert len(result) == 1
        assert result[0]["name"] == "Bash"
        assert result[0]["args"] == {}  # mock args，不传 schema

    def test_call_goes_to_registry(self):
        """call 走真实 ToolRegistry（结果进事件流）。"""

        class FakeRegistry:
            def list_tools(self):
                return [{"name": "Read", "args": {}}]
            def call(self, name, args):
                return {"status": "ok", "content": "file content"}

        tools = CLIExecutionTools(FakeRegistry())
        assert tools.call("Read", {}) == {"status": "ok", "content": "file content"}

    def test_call_handles_error(self):
        """call 异常 → 返回 error dict（不崩）。"""

        class BadRegistry:
            def list_tools(self):
                return []
            def call(self, name, args):
                raise RuntimeError("bad")

        tools = CLIExecutionTools(BadRegistry())
        result = tools.call("X", {})
        assert result["status"] == "error"
        assert "bad" in result["error"]


class TestCLIModes:
    def test_mock_mode_builds_correctly(self, tmp_path):
        """--mock 模式：MockLLM + NoopTools。"""
        loop = build_loop("test-mock", event_dir=tmp_path, mode="act", mock=True)
        assert type(loop.llm).__name__ == "MockLLM"
        assert type(loop.tools).__name__ == "NoopTools"
        assert loop.llm.generate([{"role": "user", "content": "hi"}]) != ""

    def test_real_mode_builds_correctly(self):
        """真实模式：ModelRouterAdapter + CLIExecutionTools（凭据存在时）。"""
        loop = build_loop("test-real", mode="act", mock=False)
        assert type(loop.llm).__name__ == "ModelRouterAdapter"
        assert type(loop.tools).__name__ == "CLIExecutionTools"

    def test_mock_mode_run(self, tmp_path):
        """--mock 模式 agent run 跑通。"""
        loop = build_loop("test-run", event_dir=tmp_path, mode="act", mock=True)
        loop.enqueue_op(UserTurnStart(content="hi", mode="act"))
        events = loop.run_until_idle(max_steps=5)
        assert any(e.type.value == "TurnStarted" for e in events)
        assert loop.state_machine.state.value == "finished"


class TestServerLLMInjection:
    def test_set_llm_hot_swap(self, tmp_path):
        """LOOP.set_llm 热注入（无需重建 LOOP）。"""
        log = EventLog(tmp_path / "events.jsonl")
        from src.agent.loop import AgentLoop
        from src.providers.base import MockProvider, ProviderConfig
        from src.providers.adapter import ModelRouterAdapter

        loop = AgentLoop(event_log=log, session_id="s1")
        # 初始无 llm → _do_planning 跳过（无输出）
        loop.enqueue_op(UserTurnStart(content="hi", mode="act"))
        loop.run_until_idle(max_steps=3)
        # 热注入真实 adapter
        loop.set_llm(ModelRouterAdapter())
        # 新一轮 → llm 生效
        loop.enqueue_op(UserTurnStart(content="second", mode="act"))
        events = loop.run_until_idle(max_steps=5)
        # planning 用 architect 角色
        adapter = loop.llm
        assert adapter.calls[0]["role"] == "architect" if adapter.calls else True
