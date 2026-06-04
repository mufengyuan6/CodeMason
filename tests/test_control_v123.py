"""G14 控制平面测试（v1.23 落地：策略即代码 + 运行时干预 + Loop 库）。"""

from src.loop.control import ControlPolicy, LoopLibrary, PolicyEngine, PolicyRule, RuntimeController


class TestControlPolicy:
    """策略即代码（可版本化、可审计）。"""

    def test_from_text(self):
        policy = ControlPolicy.from_text(
            "deny Bash rm_force\nrequire_approval Write *\nallow Read *",
            policy_id="prod",
        )
        assert policy.policy_id == "prod"
        assert len(policy.rules) == 3
        assert policy.rules[0].action == "deny"

    def test_from_file(self, tmp_path):
        f = tmp_path / "policy.yaml"
        f.write_text("# 策略文件\ndeny Bash sudo\n", encoding="utf-8")
        policy = ControlPolicy.from_file(str(f))
        assert policy.policy_id == "policy"
        assert policy.rules[0].tool_pattern == "Bash"

    def test_versioning(self):
        policy = ControlPolicy(policy_id="p1", version=2)
        assert policy.to_dict()["version"] == 2

    def test_export(self):
        policy = ControlPolicy(policy_id="p")
        policy.rules.append(PolicyRule(action="deny", tool_pattern="Bash"))
        d = policy.to_dict()
        assert d["rules"][0]["action"] == "deny"
        assert "policy_id" in d


class TestPolicyEngine:
    """策略执行引擎（fail-closed 语义：命中 deny 才拦）。"""

    def test_deny_matches(self):
        policy = ControlPolicy(policy_id="p")
        policy.rules.append(PolicyRule(action="deny", tool_pattern="Bash", resource_pattern="rm_force"))
        engine = PolicyEngine(policy=policy)
        result = engine.evaluate("Bash", "rm_force")
        assert result["decision"] == "deny"

    def test_allow_by_default(self):
        """未命中规则 → allow（策略只声明限制）。"""
        policy = ControlPolicy(policy_id="p")
        policy.rules.append(PolicyRule(action="deny", tool_pattern="Evil"))
        engine = PolicyEngine(policy=policy)
        assert engine.evaluate("Read", "a.py")["decision"] == "allow"

    def test_require_approval(self):
        policy = ControlPolicy(policy_id="p")
        policy.rules.append(PolicyRule(action="require_approval", tool_pattern="Write"))
        engine = PolicyEngine(policy=policy)
        assert engine.evaluate("Write", "src/x.py")["decision"] == "require_approval"

    def test_wildcard_pattern(self):
        """工具名通配（Bash* 匹配 BashTool）。"""
        policy = ControlPolicy(policy_id="p")
        policy.rules.append(PolicyRule(action="deny", tool_pattern="Bash*"))
        engine = PolicyEngine(policy=policy)
        assert engine.evaluate("BashTool", "*")["decision"] == "deny"

    def test_audit_log(self):
        policy = ControlPolicy(policy_id="p")
        engine = PolicyEngine(policy=policy)
        engine.evaluate("Read", "a.py")
        engine.evaluate("Bash", "rm_force")
        audit = engine.audit()
        assert len(audit) == 2
        assert audit[0]["tool"] == "Read"
        assert "decision" in audit[0]

    def test_disabled_rule_skipped(self):
        policy = ControlPolicy(policy_id="p")
        policy.rules.append(PolicyRule(action="deny", tool_pattern="Bash", enabled=False))
        engine = PolicyEngine(policy=policy)
        assert engine.evaluate("Bash", "*")["decision"] == "allow"  # 禁用规则不生效


class TestRuntimeController:
    """运行时干预（mid-turn：换模型/切降级/改策略/cancel）。"""

    def test_intervene(self):
        rc = RuntimeController()
        iv = rc.intervene("switch_model", target="deepseek-v4-flash", reason="效果不好换模型")
        assert iv.kind == "switch_model"
        assert iv.target == "deepseek-v4-flash"
        assert len(rc.pending()) == 1

    def test_apply_cancel_to_loop(self, tmp_path):
        """cancel 干预 → 作为 Op 入队（协议生产者，进事件流）。"""
        from src.agent import AgentLoop, EventIdGenerator
        from src.storage import EventLog

        log = EventLog(tmp_path / "s.jsonl")
        loop = AgentLoop(event_log=log, session_id="s1", event_id_gen=EventIdGenerator(prefix="t"))
        rc = RuntimeController()
        rc.intervene("cancel", reason="用户叫停")
        result = rc.apply(loop=loop)
        assert result["applied"] == 1
        # 干预作为 UserTurnCancel Op 入队
        from src.protocol.ops import UserTurnCancel

        assert any(isinstance(op, UserTurnCancel) for op in loop._pending_ops)

    def test_history(self):
        rc = RuntimeController()
        rc.intervene("update_policy", target="new-policy", reason="改策略")
        hist = rc.history()
        assert hist[0]["kind"] == "update_policy"
        assert "intervention_id" in hist[0]


class TestLoopLibrary:
    """Loop 库（预置模板：CI 清扫/Issue 分诊/变更日志）。"""

    def test_default_templates(self):
        lib = LoopLibrary()
        templates = lib.list()
        ids = {t["id"] for t in templates}
        assert "ci-cleanup" in ids
        assert "issue-triage" in ids
        assert "changelog" in ids
        assert "daily-regression" in ids

    def test_get(self):
        lib = LoopLibrary()
        t = lib.get("ci-cleanup")
        assert t is not None
        assert t.verifier == "pytest 全绿"
        assert t.stop_rule

    def test_register_custom(self):
        from src.loop.control import LoopTemplate

        lib = LoopLibrary()
        lib.register(LoopTemplate(template_id="nightly-backup", name="夜间备份", goal="备份", verifier="备份文件存在", stop_rule="完成"))
        assert lib.get("nightly-backup") is not None
