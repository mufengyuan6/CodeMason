"""G14 Loop 调度四件套测试：scheduler/worktree/judge/budget（v1.22 落地）。"""

import pytest

from src.loop import JudgeRouter, LoopBudget, LoopScheduler, WorktreeManager


class TestLoopScheduler:
    """Automations 调度触发器（schedule/事件/webhook）。"""

    def test_schedule_cron_match(self):
        """cron 匹配：HH:MM 精确 / * 通配。"""
        assert LoopScheduler._cron_matches("08:00", "08:00") is True
        assert LoopScheduler._cron_matches("08:00", "09:00") is False
        assert LoopScheduler._cron_matches("* *", "12:34") is True
        assert LoopScheduler._cron_matches("08:*", "08:15") is True

    def test_schedule_fires_rule(self):
        sched = LoopScheduler()
        sched.add_schedule("r1", "* *", "每分钟任务")  # * * = 每分钟
        fired = sched.check_schedule()
        assert any(r.triggered for r in fired)

    def test_event_trigger(self):
        sched = LoopScheduler()
        sched.add_event_trigger("pr-review", "pull_request_opened", "review PR {title}")
        results = sched.on_event("pull_request_opened")
        assert len(results) == 1
        assert results[0].rule_id == "pr-review"

    def test_webhook_with_payload(self):
        sched = LoopScheduler()
        sched.add_webhook("gh-webhook", "/api/webhook/github", "处理 {title}")
        results = sched.on_webhook("/api/webhook/github", payload={"title": "登录修复"})
        assert results[0].task == "处理 登录修复"  # 模板替换

    def test_enqueue_callback(self):
        """触发器作为协议生产者（enqueue → 内核）。"""
        received = []

        def fake_enqueue(task, trigger_type):
            received.append((task, trigger_type))
            return True

        sched = LoopScheduler(enqueue=fake_enqueue)
        sched.add_event_trigger("t1", "test_failed", "修复测试失败")
        sched.on_event("test_failed")
        assert received == [("修复测试失败", "event")]

    def test_inactive_rule_not_fired(self):
        sched = LoopScheduler()
        rule = sched.add_event_trigger("r", "x", "任务")
        rule.active = False
        assert sched.on_event("x") == []


class TestWorktreeManager:
    """git worktree 并行隔离（无 git 环境优雅降级）。"""

    def test_no_git_repo_graceful(self, tmp_path):
        """非 git 仓库 → worktrees_supported False（不崩溃）。"""
        mgr = WorktreeManager(str(tmp_path))
        assert mgr.worktrees_supported() is False
        assert mgr.create("agent-1") is None  # 优雅降级

    def test_git_repo_worktrees(self, tmp_path):
        """真实 git 仓库（若 git 可用）→ 创建隔离工作树。"""
        import subprocess

        # 初始化 git 仓库
        r = subprocess.run(["git", "init", "-b", "main"], capture_output=True, text=True, cwd=str(tmp_path))
        if r.returncode != 0:
            pytest.skip("git init 失败")
        subprocess.run(["git", "config", "user.email", "t@t.t"], capture_output=True, cwd=str(tmp_path))
        subprocess.run(["git", "config", "user.name", "t"], capture_output=True, cwd=str(tmp_path))
        (tmp_path / "a.py").write_text("print(1)")
        subprocess.run(["git", "add", "."], capture_output=True, cwd=str(tmp_path))
        subprocess.run(["git", "commit", "-m", "init"], capture_output=True, cwd=str(tmp_path))

        mgr = WorktreeManager(str(tmp_path))
        if not mgr.worktrees_supported():
            pytest.skip("git worktree 不支持")
        wt = mgr.create("agent-1")
        if wt is None:
            pytest.skip("worktree 创建失败（环境限制）")
        assert wt.branch.startswith("agent/")
        assert len(mgr.list()) >= 1


class TestJudgeRouter:
    """独立 judge 模型族（防 Verifier Theater）。"""

    def test_judge_different_provider(self):
        """生成 A 家 → 验证 B 家（不同厂商）。"""
        router = JudgeRouter(generation_provider="xfyun")
        assert router.is_independent() is True
        assert router.judge_provider != "xfyun"

    def test_resolve_roles(self):
        router = JudgeRouter(generation_provider="openai")
        assert router.resolve("editor") == "openai"  # 生成走 A 家
        assert router.resolve("judge") == "anthropic"  # 验证强制 B 家

    def test_custom_judge(self):
        router = JudgeRouter(generation_provider="xfyun", judge_provider="local")
        assert router.resolve("judge") == "local"


class TestLoopBudget:
    """loop token 硬预算（防 Token Burn）。"""

    def test_within_limit(self):
        budget = LoopBudget(hard_limit=10_000)
        budget.record(1000)
        assert budget.exceeded() is False
        assert budget.remaining() == 9000

    def test_trip_on_overflow(self):
        budget = LoopBudget(hard_limit=10_000)
        budget.record(6_000)
        budget.record(6_000)  # 12000 > 10000
        assert budget.exceeded() is True  # 超限熔断
        assert budget.state.tripped_at is not None

    def test_snapshot(self):
        budget = LoopBudget(hard_limit=100, budget_id="loop-x")
        budget.record(30)
        snap = budget.snapshot()
        assert snap["budget_id"] == "loop-x"
        assert snap["used_tokens"] == 30
        assert snap["usage_ratio"] == 0.3

    def test_reset(self):
        budget = LoopBudget(hard_limit=100)
        budget.record(200)
        assert budget.exceeded() is True
        budget.reset()
        assert budget.exceeded() is False
        assert budget.remaining() == 100
