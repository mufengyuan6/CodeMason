"""Phase 2 测试：shell 黑名单 + 审批 + 权限策略。"""

from src.security import ApprovalManager, SecurityPolicy, ShellGuard


class TestShellGuard:
    def test_rm_rf_root_blocked(self):
        guard = ShellGuard()
        result = guard.check("rm -rf /")
        assert result["blocked"] is True
        assert result["risk_level"] == "red"

    def test_sudo_blocked(self):
        guard = ShellGuard()
        assert guard.check("sudo apt install x")["blocked"] is True

    def test_sed_i_blocked(self):
        guard = ShellGuard()
        assert guard.check("sed -i 's/a/b/' file.txt")["blocked"] is True

    def test_fork_bomb_blocked(self):
        guard = ShellGuard()
        assert guard.check(":(){ :|:& };:")["blocked"] is True

    def test_safe_command_green(self):
        guard = ShellGuard()
        result = guard.check("ls -la")
        assert result["blocked"] is False
        assert result["risk_level"] == "green"

    def test_test_command_yellow(self):
        guard = ShellGuard()
        result = guard.check("pytest tests/")
        assert result["blocked"] is False
        assert result["risk_level"] == "green"

    def test_network_command_yellow(self):
        guard = ShellGuard()
        result = guard.check("pip install requests")
        assert result["blocked"] is False
        assert result["risk_level"] == "yellow"

    def test_tmp_cleanup_safe_override(self):
        guard = ShellGuard()
        result = guard.check("rm -rf /tmp/build-temp")
        assert result["blocked"] is False


class TestApprovalManager:
    def test_create_and_respond(self):
        am = ApprovalManager()
        record = am.create("Bash", "执行命令", "rm -rf /", "red")
        assert record.status == "pending"
        responded = am.respond(record.approval_id, "approve")
        assert responded.status == "approved"

    def test_reject(self):
        am = ApprovalManager()
        record = am.create("Bash", "执行命令", "rm -rf /", "red")
        am.respond(record.approval_id, "reject")
        assert am.get(record.approval_id).status == "rejected"

    def test_edit_with_command(self):
        am = ApprovalManager()
        record = am.create("Bash", "执行命令", "rm -rf /", "red")
        am.respond(record.approval_id, "edit", edited_command="rm -rf /tmp/x")
        r = am.get(record.approval_id)
        assert r.status == "edited"
        assert r.edited_command == "rm -rf /tmp/x"

    def test_idempotent_response(self):
        """重复响应只生效一次（防重放，G5）。"""
        am = ApprovalManager()
        record = am.create("Bash", "c", "rm -rf /", "red")
        am.respond(record.approval_id, "approve")
        am.respond(record.approval_id, "reject")  # 已 approved，忽略
        assert am.get(record.approval_id).status == "approved"

    def test_autonomy_levels(self):
        """G8 分级自主度：Level 3 低风险全自动。"""
        am3 = ApprovalManager(autonomy_level=3)
        assert am3.needs_approval("Bash", "pip install x", "yellow") is False
        assert am3.needs_approval("Bash", "rm -rf /", "red") is True  # 高危仍强制
        am1 = ApprovalManager(autonomy_level=1)
        assert am1.needs_approval("Bash", "pip install x", "yellow") is True
        assert am1.needs_approval("Read", "a.py", "green") is False

    def test_audit_log(self):
        am = ApprovalManager()
        r = am.create("Bash", "c", "rm -rf /", "red")
        am.respond(r.approval_id, "approve", operator="alice")
        log = am.audit_log()
        assert len(log) == 1
        assert log[0]["operator"] == "alice"
        assert log[0]["command"] == "rm -rf /"


class TestSecurityPolicy:
    def test_read_green(self):
        policy = SecurityPolicy()
        d = policy.evaluate("Read", {"path": "a.py"})
        assert d.category == "read"
        assert d.risk_level == "green"
        assert d.needs_approval is False

    def test_write_yellow(self):
        policy = SecurityPolicy()
        d = policy.evaluate("Write", {"path": "a.py"})
        assert d.category == "write"
        assert d.risk_level == "yellow"
        assert d.needs_approval is True

    def test_exec_dangerous_red(self):
        policy = SecurityPolicy()
        policy.guard = ShellGuard()
        d = policy.evaluate("Bash", {"command": "rm -rf /"})
        assert d.category == "exec"
        assert d.risk_level == "red"
        assert d.needs_approval is True

    def test_unknown_tool_red(self):
        policy = SecurityPolicy()
        d = policy.evaluate("EvilTool", {})
        assert d.risk_level == "red"
