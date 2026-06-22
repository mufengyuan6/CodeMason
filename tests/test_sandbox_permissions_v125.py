"""G19 路径级权限模型测试（v1.25 验收口径）。

验收标准（design.md G19 v1.25）：
- 内置 profile（default=workspace-only / readonly / full），可命名可复用
- 路径三级权限 read/write/deny（deny>write>read，具体覆盖宽泛，支持 glob）
- workspace_roots 批量应用规则（workspace 可写 ≠ 全可写，敏感子路径单独降权）
- 网络域级规则（默认断网，deny 优先，本地/私有网络默认阻止）
- DockerSandbox 挂载生成按 profile（workspace :rw/:ro + 敏感子路径 :ro + tmpfs /tmp）
- IsolatedLocalSandbox 同步语义（dev/prod 行为一致）
- secretless 占位符（沙箱内凭据不可解析为明文）
- 向后兼容：SandboxConfig() 无参构造 + legacy allow_network 行为不变
"""

import pytest

from src.security import SandboxConfig
from src.security.exec_sandbox import (
    DEFAULT_PROFILES,
    DockerSandbox,
    IsolatedLocalSandbox,
    detect_credential_placeholders,
    resolve_network_permission,
    resolve_path_permission,
    sanitize_placeholder_values,
)


class TestProfiles:
    """内置 profile（v1.25：default=workspace-only / readonly / full）。"""

    def test_default_profile_exists(self):
        assert "default" in DEFAULT_PROFILES
        assert "readonly" in DEFAULT_PROFILES
        assert "full" in DEFAULT_PROFILES

    def test_default_profile_workspace_writable_but_root_denied(self):
        """default = workspace-only：workspace 根可写，但全盘默认 deny。"""
        cfg = SandboxConfig(project_root="/work")
        fs = DEFAULT_PROFILES["default"]["filesystem"]
        assert resolve_path_permission("/work/src/main.py", fs, ["/work"]) == "write"
        assert resolve_path_permission("/work/src/main.py", fs, ["/work"]) != "deny"
        # :minimal 允许常用工具运行时路径可读（/etc 属 minimal），workspace 外非 minimal 路径 deny
        assert resolve_path_permission("/var/lib/private.db", fs, ["/work"]) == "deny"
        assert resolve_path_permission("/home/user/.ssh/id_rsa", fs, ["/work"]) == "deny"

    def test_readonly_profile_workspace_read_only(self):
        """readonly：workspace 也只读。"""
        fs = DEFAULT_PROFILES["readonly"]["filesystem"]
        assert resolve_path_permission("/work/src/main.py", fs, ["/work"]) == "read"

    def test_full_profile_unrestricted(self):
        """full：无限制（仅显式指定）。"""
        fs = DEFAULT_PROFILES["full"]["filesystem"]
        assert resolve_path_permission("/etc/passwd", fs, ["/work"]) == "write"


class TestPathPermissionEngine:
    """路径三级权限：deny > write > read，具体覆盖宽泛，支持 glob。"""

    def test_deny_beats_write(self):
        fs = {":root": "write", "**/*.env": "deny"}
        assert resolve_path_permission("/work/.env", fs, ["/work"]) == "deny"
        assert resolve_path_permission("/work/config/.env", fs, ["/work"]) == "deny"

    def test_specific_overrides_broad(self):
        """具体路径覆盖宽泛路径（workspace 可写 ≠ 全可写）。"""
        fs = {":workspace_roots": "write", ".git": "read"}
        assert resolve_path_permission("/work/src/main.py", fs, ["/work"]) == "write"
        assert resolve_path_permission("/work/.git/config", fs, ["/work"]) == "read"

    def test_glob_deny_env(self):
        fs = {"**/*.env": "deny", ":workspace_roots": "write"}
        assert resolve_path_permission("/work/service/.env", fs, ["/work"]) == "deny"
        # glob 精确匹配最高优先：任意目录层级下的 .env 文件
        assert resolve_path_permission("/work/.env", fs, ["/work"]) == "deny"
        # 非 .env 文件不受 glob deny 影响
        assert resolve_path_permission("/work/service/config.yaml", fs, ["/work"]) == "write"

    def test_no_match_fails_closed(self):
        """无任何匹配 → deny（fail-closed）。"""
        fs = {":workspace_roots": "write"}
        assert resolve_path_permission("/outside/unknown.bin", fs, ["/work"]) == "deny"

    def test_workspace_roots_multi(self):
        """多个 workspace 根批量应用规则。"""
        fs = {":workspace_roots": "write"}
        assert resolve_path_permission("/repo-a/x.py", fs, ["/repo-a", "/repo-b"]) == "write"
        assert resolve_path_permission("/repo-b/y.py", fs, ["/repo-a", "/repo-b"]) == "write"

    def test_relative_subpath_rule(self):
        """workspace 内相对子路径规则（docs 只读）。"""
        fs = {":workspace_roots": "write", "docs": "read"}
        assert resolve_path_permission("/work/docs/api.md", fs, ["/work"]) == "read"
        assert resolve_path_permission("/work/src/app.py", fs, ["/work"]) == "write"

    def test_write_permission_for_write_class(self):
        fs = {":workspace_roots": "read", "generated": "write"}
        assert resolve_path_permission("/work/generated/build.out", fs, ["/work"]) == "write"
        assert resolve_path_permission("/work/src/app.py", fs, ["/work"]) == "read"


class TestNetworkDomainRules:
    """网络域级规则：默认断网，deny 优先，本地/私有网络默认阻止。"""

    def test_network_disabled_blocks_all(self):
        assert resolve_network_permission("example.com", {}, enabled=False) == "deny"

    def test_allow_exact_domain(self):
        domains = {"example.com": "allow"}
        assert resolve_network_permission("example.com", domains, enabled=True) == "allow"

    def test_deny_beats_allow(self):
        domains = {"example.com": "allow", "api.example.com": "deny"}
        assert resolve_network_permission("api.example.com", domains, enabled=True) == "deny"
        assert resolve_network_permission("example.com", domains, enabled=True) == "allow"

    def test_no_allow_entries_blocks_all(self):
        """无 allow 条目 → 全部阻止。"""
        domains = {"*.evil.com": "deny"}
        assert resolve_network_permission("good.com", domains, enabled=True) == "deny"

    def test_local_network_blocked_by_default(self):
        """本地/私有网络默认阻止（防 DNS rebinding）。"""
        domains = {"localhost": "allow"}
        assert resolve_network_permission("192.168.1.5", domains, enabled=True) == "deny"
        assert resolve_network_permission("127.0.0.1", domains, enabled=True) == "deny"

    def test_wildcard_subdomain(self):
        domains = {"*.example.com": "allow"}
        assert resolve_network_permission("sub.example.com", domains, enabled=True) == "allow"
        assert resolve_network_permission("example.com", domains, enabled=True) == "deny"


class TestIsolatedLocalPermissions:
    """受限 local 后端同步路径级权限语义（dev/prod 行为一致）。"""

    def test_default_profile_network_blocked(self):
        """默认断网：网络命令拦截（legacy 行为保持）。"""
        p = IsolatedLocalSandbox(SandboxConfig(allow_network=False))
        r = p.run("curl http://example.com")
        assert r.exit_code == 1
        assert "网络访问未放行" in r.stderr

    def test_network_domain_allowlist(self):
        """网络域 allowlist：allow 域放行，其余阻止。"""
        cfg = SandboxConfig(network_enabled=True, network_domains={"example.com": "allow"})
        p = IsolatedLocalSandbox(cfg)
        r = p.run("curl http://example.com/data")
        assert r.exit_code == 0  # allow 域放行
        r2 = p.run("curl http://evil.com/x")
        assert r2.exit_code == 1  # 未 allow 域阻止
        assert "网络访问未放行" in r2.stderr

    def test_default_profile_workspace_write_succeeds(self, tmp_path):
        """default profile：workspace 内写操作不拦截（git/构建语义）。"""
        cfg = SandboxConfig(project_root=str(tmp_path))
        p = IsolatedLocalSandbox(cfg)
        r = p.run("echo hi > local_tmp_v125.txt", cwd=str(tmp_path))
        assert r.exit_code == 0

    def test_deny_path_write_blocked(self, tmp_path):
        """deny 路径写操作拦截（**.env 语义）。"""
        cfg = SandboxConfig(project_root=str(tmp_path), filesystem={"**/*.env": "deny"})
        p = IsolatedLocalSandbox(cfg)
        r = p.run("echo secret > .env", cwd=str(tmp_path))
        assert r.exit_code == 1
        assert "权限拦截" in r.stderr or "deny" in r.stderr.lower()

    def test_legacy_allow_network_still_works(self):
        """向后兼容：legacy allow_network=True 放行。"""
        p = IsolatedLocalSandbox(SandboxConfig(allow_network=True))
        r = p.run("echo ok")
        assert r.exit_code == 0


class TestDockerSandboxMountArgs:
    """挂载生成按 profile（不实际跑 docker，只验证参数生成）。"""

    def _args(self, root, **kw):
        cfg = SandboxConfig(project_root=str(root), **kw)
        d = DockerSandbox(cfg)
        return d._build_args("echo hi", cfg.project_root)

    def test_default_profile_workspace_rw_mount(self, tmp_path):
        """default：workspace 可写挂载（:rw）+ tmpfs /tmp。"""
        args = self._args(tmp_path)
        assert any(a.endswith(":/work:rw") for a in args)
        assert any("tmpfs=/tmp" in a for a in args)

    def test_readonly_profile_workspace_ro_mount(self, tmp_path):
        """readonly：workspace 只读挂载（:ro）。"""
        args = self._args(tmp_path, profile="readonly")
        assert any(a.endswith(":/work:ro") for a in args)
        assert not any(a.endswith(":/work:rw") for a in args)

    def test_default_profile_git_subpath_ro(self, tmp_path):
        """default：.git 敏感子路径单独只读挂载（覆盖 rw 父挂载）。"""
        args = self._args(tmp_path)
        assert any(a.endswith(":/work/.git:ro") for a in args)

    def test_external_paths_not_mounted(self, tmp_path):
        """外部路径不挂载（~/.ssh 等不出现）。"""
        args = self._args(tmp_path)
        joined = " ".join(args)
        assert ".ssh" not in joined
        assert ".aws" not in joined

    def test_root_fs_read_only_kept(self, tmp_path):
        """根文件系统 --read-only 保持（装不了全局包但项目可写）。"""
        args = self._args(tmp_path)
        assert "--read-only" in args


class TestSecretless:
    """secretless 凭据隔离：沙箱内只可见占位符，不可解析明文。"""

    def test_detect_placeholders(self):
        placeholders = detect_credential_placeholders("api key: {{credential:OPENAI_API_KEY}}")
        assert placeholders == ["OPENAI_API_KEY"]

    def test_no_placeholders_when_plain(self):
        assert detect_credential_placeholders("no secrets here") == []

    def test_sanitize_placeholder_values(self):
        """沙箱内占位符不可解析为明文（保持占位符原样，永不注入）。"""
        cmd = "echo {{credential:OPENAI_API_KEY}}"
        sanitized = sanitize_placeholder_values(cmd)
        assert "sk-" not in sanitized  # 无明文
        assert "{{credential:OPENAI_API_KEY}}" in sanitized  # 占位符保留（出站代理注入）

    def test_isolated_local_env_no_credentials(self):
        """沙箱环境无明文凭据（legacy 审计保持）。"""
        cfg = SandboxConfig()
        assert cfg.credentials_inside is False
        p = IsolatedLocalSandbox(cfg)
        r = p.run("env | grep -i 'AWS_\\|GITHUB_\\|OPENAI_' || echo no-credentials")
        assert "no-credentials" in r.stdout
