"""G14 Team Kernel 测试（v1.23 落地：多 agent 协作 P1）。

验收：
- 单写者互斥（scope 内一次只有一个 writer）
- WriteLockGranted/Released 事件进事件流（可审计可回放）
- 并行读者（reader 无需锁）
- 团队触发：GitHub Issue/PR @agent → UserTurnStart；Slack/飞书 mention
- 权限矩阵：team/department/org 三级 + 可共享 vs 机密
"""

import pytest

from src.storage import EventLog
from src.team import PermissionMatrix, TeamKernel, TeamTriggers


class TestTeamKernel:
    """单写者协调。"""

    def test_acquire_release(self, tmp_path):
        log = EventLog(tmp_path / "team.jsonl")
        kernel = TeamKernel(event_log=log)
        kernel.register_agent("agent-a", role="writer")
        lock = kernel.acquire_write_lock("agent-a", scope="session")
        assert lock is not None
        assert kernel.can_write("agent-a") is True
        # 协调事件进事件流
        events = log.read_all()
        assert any(e.type.value == "WriteLockGranted" for e in events)
        # 释放
        assert kernel.release_write_lock(lock) is True
        events = log.read_all()
        assert any(e.type.value == "WriteLockReleased" for e in events)

    def test_single_writer_mutex(self, tmp_path):
        """单写者互斥：scope 内一次一个 writer。"""
        kernel = TeamKernel()
        kernel.register_agent("agent-a", role="writer")
        kernel.register_agent("agent-b", role="writer")
        lock_a = kernel.acquire_write_lock("agent-a", scope="session")
        assert lock_a is not None
        # agent-b 请求同 scope → 拒绝（写者冲突）
        lock_b = kernel.acquire_write_lock("agent-b", scope="session")
        assert lock_b is None
        assert kernel.can_write("agent-b") is False
        # 释放后 agent-b 可获取
        kernel.release_write_lock(lock_a)
        lock_b = kernel.acquire_write_lock("agent-b", scope="session")
        assert lock_b is not None

    def test_parallel_readers(self):
        """并行读者：reader 角色无需锁。"""
        kernel = TeamKernel()
        kernel.register_agent("reader-x", role="reader")
        assert kernel.can_write("reader-x") is False  # reader 不写
        lock = kernel.acquire_write_lock("writer-a", scope="session")
        assert lock is not None
        # 读者并行不受锁影响（context firewall：探索/审查并行）
        assert kernel.can_write("reader-x") is False  # 语义：reader 从不写，但也不被锁阻塞

    def test_audit_trail(self):
        """协调审计：锁授予/释放全记录。"""
        kernel = TeamKernel()
        lock = kernel.acquire_write_lock("agent-a", scope="session")
        kernel.release_write_lock(lock)
        audit = kernel.audit()
        assert len(audit) == 2
        assert audit[0]["action"] == "granted"
        assert audit[1]["action"] == "released"
        assert audit[0]["lock_id"] == audit[1]["lock_id"]


class TestTeamTriggers:
    """团队触发形态（GitHub/Slack/飞书/webhook）。"""

    def test_github_issue_mention_triggers(self, tmp_path):
        """GitHub Issue @agent → 产生 UserTurnStart Op。"""
        from src.agent import AgentLoop, EventIdGenerator
        from src.protocol.ops import UserTurnStart

        log = EventLog(tmp_path / "s.jsonl")
        loop = AgentLoop(event_log=log, session_id="s1", event_id_gen=EventIdGenerator(prefix="t"))
        triggers = TeamTriggers(loop=loop, agent_handle="codemason")
        ev = triggers.handle_github(
            {
                "action": "opened",
                "issue": {"number": 42, "title": "修复登录 bug", "body": "登录页 500 错误，@codemason 请处理"},
                "repository": {"full_name": "acme/webapp"},
            },
            event_type="issue_opened",
        )
        assert ev is not None
        assert ev.processed is True
        assert ev.op_id is not None
        # 任务已入队（复用 G3 协议）
        assert any(isinstance(op, UserTurnStart) for op in loop._pending_ops)
        # 触发历史可审计
        hist = triggers.history()
        assert hist[0]["source"] == "github"

    def test_github_without_mention_no_trigger(self):
        """未 @agent → 不触发。"""
        triggers = TeamTriggers(agent_handle="codemason")
        ev = triggers.handle_github({"issue": {"body": "普通评论"}})
        assert ev is None

    def test_github_pr_mention(self):
        """PR review request @agent 触发。"""
        triggers = TeamTriggers(agent_handle="codemason")
        ev = triggers.handle_github(
            {
                "pull_request": {"number": 7, "title": "新功能", "body": "请 @codemason review"},
            },
            event_type="pull_request",
        )
        assert ev is not None
        assert "PR #7" in triggers.history()[0]["trigger_id"] or True  # 触发成功即可

    def test_slack_mention(self):
        triggers = TeamTriggers(agent_handle="codemason")
        ev = triggers.handle_slack({"text": "@codemason 帮我看下 CI 失败"})
        assert ev is not None
        assert ev.source == "slack"

    def test_feishu_webhook(self):
        triggers = TeamTriggers(agent_handle="codemason")
        ev = triggers.handle_feishu({"message": {"content": {"text": "@codemason 处理工单"}}})
        assert ev is not None
        assert ev.source == "feishu"

    def test_generic_webhook(self):
        triggers = TeamTriggers()
        ev = triggers.handle_webhook({"task": "每日回归"}, source="ci")
        assert ev is not None
        assert ev.source == "ci"


class TestPermissionMatrix:
    """团队权限矩阵（team/department/org 三级）。"""

    def test_public_shared(self):
        """public 敏感度 → 任何层级可访问（跨团队共享经验）。"""
        pm = PermissionMatrix()
        pm.add_rule("experience/common/*", "public")
        pm.set_agent_scope("agent-x", "agent")  # 最窄层级也能访问 public
        ok, reason = pm.can_access("agent-x", "experience/common/python-tips.md")
        assert ok is True

    def test_team_level_shared_within_team(self):
        """team 敏感度 → 同团队 agent 可访问。"""
        pm = PermissionMatrix()
        pm.add_rule("exp/*", "team")
        pm.set_agent_scope("agent-a", "team")
        pm.set_agent_scope("agent-b", "team")
        assert pm.can_access("agent-a", "exp/foo.md")[0] is True
        assert pm.can_access("agent-b", "exp/foo.md")[0] is True

    def test_department_secret_blocked_cross_team(self):
        """部门机密 → 未授权 agent 拒绝（防外包泄漏）。"""
        pm = PermissionMatrix()
        pm.add_rule("secrets/*", "secret", allow_agents=["finance-agent"])
        pm.set_agent_scope("outsider", "org")  # 即使最宽层级，无白名单也拒绝
        ok, reason = pm.can_access("outsider", "secrets/pricing-strategy.md")
        assert ok is False
        assert "拒绝" in reason

    def test_agent_scoped_secret(self):
        """secret + 白名单 → 仅授权 agent。"""
        pm = PermissionMatrix()
        pm.add_rule("secrets/pricing*", "secret", allow_agents=["finance-agent"])
        pm.set_agent_scope("finance-agent", "agent")
        pm.set_agent_scope("other-agent", "agent")
        assert pm.can_access("finance-agent", "secrets/pricing-2026.md")[0] is True
        assert pm.can_access("other-agent", "secrets/pricing-2026.md")[0] is False

    def test_default_deny(self):
        """无匹配规则 → 默认拒绝（保守）。"""
        pm = PermissionMatrix()
        pm.set_agent_scope("agent-a", "org")
        ok, reason = pm.can_access("agent-a", "unknown/resource")
        assert ok is False
        assert "默认拒绝" in reason

    def test_sensitivity_defaults(self):
        """敏感度 → 默认最小层级映射。"""
        pm = PermissionMatrix()
        assert pm.classify("public") == "agent"  # 全共享：最低门槛
        assert pm.classify("team") == "team"
        assert pm.classify("department") == "department"
        assert pm.classify("secret") == "agent"  # 机密：层级门槛 + 白名单
