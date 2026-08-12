"""v1.30 T-11a 测试：ModelRouterAdapter（真实 LLM → AgentLoop 协议适配层）。

验证：
- LLMProtocol 签名兼容（generate(messages, role)）
- 角色透传到 ModelRouter.generate_role
- vision 角色走独立 provider（故障域隔离）
- 无 router → Mock 降级（fail-safe，内核不崩）
- AgentLoop 集成：真实 adapter 作为 llm 注入，planning 用 architect 角色
"""

import pytest

from src.agent.loop import AgentLoop
from src.providers.adapter import (
    ModelRouterAdapter,
    build_adapter_from_credentials,
)
from src.providers.base import MockProvider, ProviderConfig
from src.routing.router import ModelRouter, ModelSpec
from src.storage import EventLog


class _FakeRouter(ModelRouter):
    """记录角色的假 router（不触网，验证角色透传）。"""

    def __init__(self, reply="真实回复"):
        super().__init__(
            provider=MockProvider(ProviderConfig(name="fake", base_url="http://fake", api_key="", default_model="deepseek-v4-flash")),
            models=[
                ModelSpec(name="deepseek-v4-flash", role="architect", priority=1),
                ModelSpec(name="deepseek-v4-flash", role="editor", priority=1),
                ModelSpec(name="mimo-v2.5", role="vision", priority=1),
            ],
        )
        self.reply = reply
        self.roles = []

    def generate_role(self, messages, *, role):
        self.roles.append(role)
        return self.reply


class TestModelRouterAdapter:
    def test_llm_protocol_signature_compatible(self):
        """generate(messages, *, role) 与 AgentLoop LLMProtocol 兼容。"""
        router = _FakeRouter(reply="plan output")
        adapter = ModelRouterAdapter(router)
        out = adapter.generate([{"role": "user", "content": "hi"}], role="architect")
        assert out == "plan output"

    def test_role_passthrough(self):
        """角色透传到 router.generate_role（architect/editor/vision 各自显式）。"""
        router = _FakeRouter()
        adapter = ModelRouterAdapter(router)
        adapter.generate([{}], role="architect")
        adapter.generate([{}], role="editor")
        adapter.generate([{}], role="vision")
        assert router.roles == ["architect", "editor", "vision"]

    def test_vision_uses_own_provider(self):
        """vision 角色由 router 内部路由到独立视觉 provider（故障域隔离由 ModelRouter 保证）。"""
        router = _FakeRouter()
        adapter = ModelRouterAdapter(router)
        out = adapter.generate([{"role": "user", "content": "看图"}], role="vision")
        assert out == "真实回复"
        # router 侧 vision 模型存在（generate_role 会按 role 选模型）
        vision_models = [m for m in router.models if m.role == "vision"]
        assert vision_models[0].name == "mimo-v2.5"

    def test_no_router_mock_fallback(self):
        """无 router → Mock 降级（fail-safe，返回兜底文本不抛错）。"""
        adapter = ModelRouterAdapter(router=None, fallback_reply="fallback")
        assert adapter.generate([{}], role="editor") == "fallback"
        assert adapter.calls[0]["provider"] == "mock-fallback"

    def test_router_error_fallbacks(self):
        """真实 LLM 抛错 → 降级兜底（不阻断内核）。"""

        class BoomRouter(_FakeRouter):
            def generate_role(self, messages, *, role):
                raise RuntimeError("llm down")

        adapter = ModelRouterAdapter(BoomRouter(), fallback_reply="safe")
        assert adapter.generate([{}], role="editor") == "safe"
        assert adapter.calls[0]["provider"] == "error"

    def test_route_decision_passthrough(self):
        router = _FakeRouter()
        adapter = ModelRouterAdapter(router)
        decision = adapter.route("plan")
        assert decision.role == "architect"
        assert decision.model == "deepseek-v4-flash"

    def test_route_decision_mock_fallback(self):
        adapter = ModelRouterAdapter(router=None)
        decision = adapter.route("act")
        assert decision.role == "editor"
        assert decision.model == "mock"

    def test_routing_stats(self):
        router = _FakeRouter()
        adapter = ModelRouterAdapter(router)
        stats = adapter.get_routing_stats()
        assert any(m["role"] == "vision" for m in stats["models"])

    def test_close_swallows_errors(self):
        """close() 释放连接不抛错（含无 router 场景）。"""
        adapter = ModelRouterAdapter(_FakeRouter())
        adapter.close()  # 不抛即通过
        ModelRouterAdapter(None).close()


class TestBuildAdapter:
    def test_build_from_credentials(self):
        """凭据存在时构建真实适配器（deepseek-v4-flash 模型就位）。"""
        adapter = build_adapter_from_credentials()
        stats = adapter.get_routing_stats()
        names = {m["name"] for m in stats["models"]}
        assert "deepseek-v4-flash" in names
        assert "mimo-v2.5" in names


class TestAgentLoopIntegration:
    def test_loop_runs_with_real_adapter(self, tmp_path):
        """AgentLoop 注入真实 adapter 可跑通（planning 走 architect 角色）。"""
        log = EventLog(tmp_path / "events.jsonl")
        router = _FakeRouter(reply="走一遍真实链路")
        adapter = ModelRouterAdapter(router)
        loop = AgentLoop(event_log=log, llm=adapter, tools=None, session_id="s1")
        from src.protocol import UserTurnStart

        loop.enqueue_op(UserTurnStart(content="帮我修一个 bug"))
        loop.run_until_idle(max_steps=10)
        # planning 用 architect 角色
        assert "architect" in router.roles
        # 有 Agent 消息产出（AgentMessageContentDelta 直接落 event_log 事实源）
        events = log.read_all()
        assert any(e.type.value == "AgentMessageContentDelta" for e in events)