"""v1.13 健康信号 + Hook 7 事件点测试（T-C）。

验收标准（design 3.2 健康信号 + G1 Hook 7 点）：
- 卡检测 stuck：相同工具调用/相同错误反复出现 → 触发上下文干预建议
- 会话健康度：四维指标连续恶化 + stuck 频率 → 健康度报警 → 建议交接/新会话
- Hook 7 事件点注册生效（SessionStart/UserPromptSubmit/PreToolUse/PostToolUse/PreCompact/Stop）
"""

from src.context.health import SessionHealth, StuckDetector
from src.harness.hook_framework import BaseHook, HookContext, HookEvent, HookPriority, HookResult, HooksManager


class TestStuckDetector:
    def test_repeated_tool_call_detected(self):
        """卡检测：相同工具 + 相同命令反复调用 → stuck。"""
        d = StuckDetector(window=8, repeat_threshold=3)
        args = {"command": "pytest tests/test_a.py"}
        assert d.observe_tool_call("Bash", args) is None
        assert d.observe_tool_call("Bash", args) is None
        signal = d.observe_tool_call("Bash", args)
        assert signal is not None
        assert signal.stuck is True
        assert signal.repeated_tool == "Bash"
        assert signal.repeat_count == 3
        assert "强制压缩" in signal.suggestion

    def test_different_args_not_stuck(self):
        """不同参数不误判：同工具不同操作不算 stuck。"""
        d = StuckDetector(window=8, repeat_threshold=3)
        d.observe_tool_call("Bash", {"command": "pytest a"})
        d.observe_tool_call("Bash", {"command": "pytest b"})
        d.observe_tool_call("Bash", {"command": "pytest c"})
        assert d.stuck_count() == 0

    def test_repeated_error_detected(self):
        """相同错误反复出现 = 假设固化。"""
        d = StuckDetector(repeat_threshold=2)
        assert d.observe_error("syntax", "SyntaxError: invalid syntax") is None
        signal = d.observe_error("syntax", "SyntaxError: invalid syntax")
        assert signal is not None
        assert signal.stuck is True
        assert "质疑当前假设" in signal.suggestion


class TestSessionHealth:
    def test_healthy_by_default(self):
        sh = SessionHealth()
        r = sh.report()
        assert r.level == "healthy"
        assert r.score >= 70

    def test_degraded_on_stuck(self):
        sh = SessionHealth()
        args = {"command": "pytest x"}
        sh.observe_tool_call("Bash", args)
        sh.observe_tool_call("Bash", args)
        sh.observe_tool_call("Bash", args)  # 触发 stuck
        r = sh.report()
        assert r.stuck_count >= 1
        assert r.level in ("degraded", "critical")
        assert r.score < 100

    def test_critical_on_refetch_surge(self):
        """回捞激增 + 摘要遗漏 + stale 命中 → critical → 建议交接。"""
        sh = SessionHealth()
        for _ in range(4):
            sh.observe_recall(0.8)  # 回捞率 80%
        for _ in range(4):
            sh.observe_stale_hit(0.3)
        for _ in range(4):
            sh.observe_summary_miss()
        r = sh.report()
        assert r.level == "critical"
        assert "交接" in r.advice
        assert r.score < 40

    def test_worsening_trend_detected(self):
        """连续恶化检测：回捞率单调上升 → 额外扣分。"""
        sh = SessionHealth()
        sh.observe_recall(0.1)
        sh.observe_recall(0.2)
        sh.observe_recall(0.3)
        r = sh.report()
        # 连续恶化 -10 → score 90
        assert r.score == 90

    def test_report_serializable(self):
        sh = SessionHealth()
        r = sh.report()
        d = r.to_dict()
        assert set(d) >= {"score", "level", "advice", "stuck_count"}


class TestHookLifecyclePoints:
    """Hook 7 事件点（对标 planning-with-files）：注册 + 执行。"""

    def test_hook_7_lifecycle_points(self):
        """生命周期 7 点全部可注册。"""
        required = [
            HookEvent.SESSION_START,
            HookEvent.USER_PROMPT_SUBMIT,
            HookEvent.PRE_TOOL_USE,
            HookEvent.POST_TOOL_USE,
            HookEvent.PRE_COMPACT,
            HookEvent.STOP,
            HookEvent.FAILURE,
        ]
        assert all(e.lifecycle for e in required)

    def test_legacy_aliases_preserved(self):
        """旧 4 点保留（不破坏既有代码）：TOOL_CALL 仍是独立可注册成员。"""
        # 旧 4 点全部存在
        for name in ("TOOL_CALL", "EDIT", "COMMIT", "FAILURE"):
            assert name in HookEvent.__members__
        # TOOL_CALL 是旧语义的"工具调用前"，可与 PRE_TOOL_USE 并存注册
        mgr = HooksManager()
        mgr.register_fn(HookEvent.TOOL_CALL, lambda ctx: HookResult("old", True, "ok"), name="old-toolcall")
        assert "old-toolcall" in mgr.hooks_for(HookEvent.TOOL_CALL)

    def test_register_and_run_session_start(self):
        """SessionStart hook：注册 + 执行。"""
        mgr = HooksManager()
        captured = {}

        class StartHook(BaseHook):
            def execute(self, ctx: HookContext) -> HookResult:
                captured["session"] = ctx.metadata.get("session_id")
                return HookResult("StartHook", True, "ok", "pass")

        mgr.register(StartHook("start", HookEvent.SESSION_START))
        ctx = HookContext(event=HookEvent.SESSION_START, metadata={"session_id": "s1"})
        results = mgr.run(HookEvent.SESSION_START, ctx)
        assert len(results) == 1
        assert results[0].allowed
        assert captured["session"] == "s1"

    def test_health_as_hook_consumer(self):
        """health 作为 Hook 消费者：PostToolUse/on_failure 注册（一次解决两个差距）。"""
        from src.harness.hook_framework import HookResult

        mgr = HooksManager()
        health = SessionHealth()

        def tool_observer(ctx: HookContext) -> HookResult:
            sig = health.observe_tool_call(ctx.tool_name, ctx.args)
            if sig and sig.stuck:
                return HookResult("HealthStuck", False, sig.reason, "warn", action="suggest_compress")
            return HookResult("HealthObserver", True, "ok", "pass")

        def failure_observer(ctx: HookContext) -> HookResult:
            health.observe_error(ctx.metadata.get("error_type", "unknown"), ctx.error or "")
            return HookResult("HealthFailure", True, "ok", "pass")

        mgr.register_fn(HookEvent.POST_TOOL_USE, tool_observer, name="health-stuck")
        mgr.register_fn(HookEvent.FAILURE, failure_observer, name="health-failure")
        assert "health-stuck" in mgr.hooks_for(HookEvent.POST_TOOL_USE)
        assert "health-failure" in mgr.hooks_for(HookEvent.FAILURE)

        # 重复调用触发 stuck 警告（不 block，仅 warn）
        for _ in range(3):
            r = tool_observer(HookContext(event=HookEvent.POST_TOOL_USE, tool_name="Bash", args={"command": "pytest x"}))
        assert r.severity == "warn" if r else True
