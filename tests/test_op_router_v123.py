"""4.1 双模型路由按 Op 分派测试（v1.23 落地：P1 升级）。

验收：
- 按 Op 类型分派：read/format 便宜模型，plan/refactor 强模型
- 路由合规审计（防软配置被绕过，OpenClaw 教训）
- 审计日志（成本归因）
"""

from src.routing import OpRouter, OpRoutingRule, OpTier


class TestOpRouter:
    """按 Op 类型分派。"""

    def test_cheap_ops(self):
        """read/format 类 → cheap（editor 便宜模型）。"""
        router = OpRouter()
        for op in ("Read", "Glob", "Grep", "WebSearch", "WebFetch", "Monitor"):
            decision = router.route(op)
            assert decision.tier == OpTier.CHEAP, op
            assert decision.model_role == "editor", op

    def test_expensive_ops(self):
        """plan/refactor/复杂 bug → expensive（architect 强模型）。"""
        router = OpRouter()
        for op in ("plan", "refactor", "Bash", "run_code", "subagent"):
            decision = router.route(op)
            assert decision.tier == OpTier.EXPENSIVE, op
            assert decision.model_role == "architect", op

    def test_standard_ops(self):
        """Write/Edit → standard。"""
        router = OpRouter()
        assert router.route("Write").tier == OpTier.STANDARD
        assert router.route("Edit").tier == OpTier.STANDARD

    def test_wildcard_fallback(self):
        """未知 Op → 兜底 standard（不崩溃）。"""
        router = OpRouter()
        assert router.route("UnknownTool").tier == OpTier.STANDARD

    def test_custom_rules(self):
        """自定义规则：新增昂贵 Op。"""
        router = OpRouter(rules=[OpRoutingRule("CodeReview", OpTier.EXPENSIVE, priority=1)])
        assert router.route("CodeReview").tier == OpTier.EXPENSIVE

    def test_audit(self):
        router = OpRouter()
        router.route("Read")
        router.route("Bash")
        audit = router.audit()
        assert len(audit) == 2
        assert audit[0]["op"] == "Read"
        assert audit[0]["tier"] == "cheap"


class TestComplianceAudit:
    """路由合规审计（防软配置绕过）。"""

    def test_compliant(self):
        router = OpRouter()
        decision = router.route("Read")
        result = router.compliance_check(current_role=decision.model_role, expected_role="editor", op_name="Read")
        assert result["ok"] is True
        assert result["violation"] is False

    def test_violation_detected(self):
        """agent 试图用便宜 Op 走强模型（绕过成本管控）→ 检出违规。"""
        router = OpRouter()
        result = router.compliance_check(current_role="architect", expected_role="editor", op_name="Read")
        assert result["ok"] is False
        assert result["violation"] is True

    def test_stats(self):
        router = OpRouter()
        router.route("Read")
        router.route("Read")
        router.route("Bash")
        stats = router.stats()
        assert stats["total_routes"] == 3
        assert stats["by_tier"]["cheap"] == 2
        assert stats["by_tier"]["expensive"] == 1
