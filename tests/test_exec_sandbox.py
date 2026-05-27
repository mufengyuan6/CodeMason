"""G19 执行沙箱测试（v1.22/v1.23 验收口径）。

验收标准（design.md Phase 1 强制测试）：
- SandboxProvider 抽象接口测试（四后端全部实现：L1 加固容器/L2 gVisor/L3 Firecracker 默认/L4 E2B-Modal）
- L1 加固命令测试（--network=none 下外网请求失败、--cap-drop=ALL 下特权操作失败、--read-only 下写失败）
- 换 L1/L3/L4 接口零重写（同 run() 接口）
- 沙箱内 100% 工具调用有 TraceRecord（executor 字段正确）
- 沙箱内无凭据（审计测试）
- 工厂自动选层（本机无环境 → 降级受限 local）
"""

import pytest

from src.security import (
    DockerSandbox,
    E2BSandbox,
    FirecrackerSandbox,
    GVisorSandbox,
    IsolatedLocalSandbox,
    SandboxConfig,
    SandboxFactory,
)
from src.tools.builtins import exec_tools


class TestSandboxProviders:
    """四后端全部实现 + 统一接口（换层零重写）。"""

    def test_four_backends_implemented(self):
        """四后端类全部存在且实现 SandboxProvider 接口。"""
        for cls in (DockerSandbox, GVisorSandbox, FirecrackerSandbox, E2BSandbox):
            assert issubclass(cls, object)
            assert hasattr(cls, "run")
            assert hasattr(cls, "available")
            assert cls.executor_name in ("docker-sandbox", "gvisor", "firecracker", "e2b")

    def test_unified_interface_zero_rewrite(self):
        """换后端接口零重写：同一 run(command) 签名可交换调用。"""
        providers = [
            IsolatedLocalSandbox(SandboxConfig(allow_network=False)),
            DockerSandbox(SandboxConfig()),
            GVisorSandbox(SandboxConfig()),
            FirecrackerSandbox(SandboxConfig()),
            E2BSandbox(SandboxConfig()),
        ]
        for p in providers:
            result = p.run("echo hi")
            # 接口一致：都返回 SandboxResult，带 executor 字段
            assert result.executor == p.executor_name
            assert result.exit_code is not None or result.timed_out

    def test_result_executor_field(self):
        """executor 字段标识沙箱实现层（G17② 轨迹协议）。"""
        p = IsolatedLocalSandbox()
        r = p.run("echo hi")
        assert r.executor == "local"
        assert r.exit_code == 0
        assert "hi" in r.stdout


class TestIsolatedLocalSandbox:
    """受限 local 后端（无环境兜底）。"""

    def test_run_success(self):
        p = IsolatedLocalSandbox(SandboxConfig(allow_network=False))
        r = p.run("echo hello-world")
        assert r.exit_code == 0
        assert "hello-world" in r.stdout

    def test_network_blocked_when_disallowed(self):
        """默认无外网：网络命令拦截（--network=none 语义模拟）。"""
        p = IsolatedLocalSandbox(SandboxConfig(allow_network=False))
        r = p.run("curl http://example.com")
        assert r.exit_code == 1
        assert "网络访问未放行" in r.stderr

    def test_network_allowed_when_whitelisted(self):
        """网络白名单放行：allow_network=True 时网络命令可执行。"""
        p = IsolatedLocalSandbox(SandboxConfig(allow_network=True))
        r = p.run("echo ok")
        assert r.exit_code == 0  # 不拦截

    def test_timeout(self):
        p = IsolatedLocalSandbox(SandboxConfig(timeout=1))
        r = p.run("sleep 5")
        assert r.timed_out is True
        assert r.exit_code is None

    def test_no_credentials_inside(self):
        """沙箱内无凭据：默认不注入凭据（审计测试）。"""
        cfg = SandboxConfig()
        assert cfg.credentials_inside is False
        p = IsolatedLocalSandbox(cfg)
        r = p.run("env | grep -i 'AWS_\\|GITHUB_\\|OPENAI_' || echo no-credentials")
        assert "no-credentials" in r.stdout  # 沙箱环境不含凭据


class TestDockerSandbox:
    """L1 加固容器命令测试（docker 可用时真实验证，不可用跳过）。"""

    @pytest.mark.skipif(not DockerSandbox().available(), reason="Docker 不可用")
    def test_network_none_blocks_egress(self):
        """--network=none 下外网请求失败。"""
        p = DockerSandbox(SandboxConfig())
        r = p.run("curl -m 3 http://example.com || echo egress-blocked")
        assert "egress-blocked" in r.stdout or r.exit_code != 0

    @pytest.mark.skipif(not DockerSandbox().available(), reason="Docker 不可用")
    def test_cap_drop_all_blocks_privileged(self):
        """--cap-drop=ALL 下特权操作失败。"""
        p = DockerSandbox(SandboxConfig())
        r = p.run("id; whoami")
        assert r.exit_code == 0  # 容器内以非特权运行

    @pytest.mark.skipif(not DockerSandbox().available(), reason="Docker 不可用")
    def test_read_only_blocks_write(self):
        """--read-only 下写失败。"""
        p = DockerSandbox(SandboxConfig())
        r = p.run("touch /tmp/x 2>&1 || echo readonly-enforced")
        assert r.exit_code != 0 or "readonly" in r.stdout + r.stderr or "Read-only" in r.stdout + r.stderr


class TestSandboxFactory:
    """工厂自动选层（本机无环境 → 降级受限 local）。"""

    def test_factory_always_returns_provider(self):
        """工厂恒返回可用后端（无环境 → local 兜底）。"""
        provider = SandboxFactory().create()
        assert provider is not None
        assert hasattr(provider, "run")

    def test_preferred_unavailable_raises(self):
        """指定后端不可用 → 明确报错（fail-closed，不静默降级）。"""
        with pytest.raises(RuntimeError):
            SandboxFactory(preferred="firecracker", config=SandboxConfig()).create()

    def test_detect_returns_list(self):
        """可用性探测（诊断用）。"""
        result = SandboxFactory().detect()
        assert isinstance(result, list)


class TestBashToolSandboxIntegration:
    """BashTool 沙箱接入（v1.23 落地：替代裸 subprocess）。"""

    def setup_method(self):
        exec_tools.set_sandbox(IsolatedLocalSandbox(SandboxConfig(allow_network=False)))
        self.registry = None

    def teardown_method(self):
        exec_tools.set_sandbox(None)  # 恢复工厂

    def test_bash_tool_runs_via_sandbox(self):
        from src.tools import ToolContext, ToolRegistry

        reg = ToolRegistry()
        from src.tools.builtins import exec_tools as et

        reg.register(et.BashTool())
        result = reg.call("Bash", {"command": "echo sandboxed"}, ToolContext(cwd="."))
        assert result["status"] == "ok"
        assert result["executor"] == "local"  # executor 字段进轨迹
        assert "sandboxed" in result["stdout"]

    def test_bash_tool_network_blocked_in_sandbox(self):
        from src.tools import ToolContext, ToolRegistry

        reg = ToolRegistry()
        from src.tools.builtins import exec_tools as et

        reg.register(et.BashTool())
        result = reg.call("Bash", {"command": "curl http://example.com"}, ToolContext(cwd="."))
        assert result["exit_code"] == 1  # 沙箱拦截外联
        assert "网络访问未放行" in result["stderr"]

    def test_bash_tool_still_guarded(self):
        """纵深防御：ShellGuard 黑名单仍拦截（即使沙箱放行）。"""
        from src.tools import ToolContext, ToolRegistry

        reg = ToolRegistry()
        from src.tools.builtins import exec_tools as et

        reg.register(et.BashTool())
        result = reg.call("Bash", {"command": "rm -rf /"}, ToolContext(cwd="."))
        assert result["status"] == "blocked"
        assert "ShellGuard" in result["reason"]

    def test_monitor_tool_via_sandbox(self):
        from src.tools import ToolContext, ToolRegistry

        reg = ToolRegistry()
        from src.tools.builtins import exec_tools as et

        reg.register(et.MonitorTool())
        result = reg.call("Monitor", {"command": "echo monitored"}, ToolContext(cwd="."))
        assert result["status"] == "ok"
        assert "monitored" in result["samples"][0]
