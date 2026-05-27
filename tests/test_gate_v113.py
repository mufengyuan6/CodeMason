"""v1.13 T-G 测试：token 台账 + 机读门禁 + attestation + subagent 协议化 + skill 绑模型。

验收标准（design 5.1/6.1/G7/G11/4.2/5.5）：
- 成本驾驶舱有数据（每 Op token/节省可查，高成本操作有预警）
- 机读验证门禁：status=passed 才算完成 + stale 检测 + fail-closed
- staging attestation：SHA256 完整性校验，篡改拒绝 apply
- 子代理返回协议化：findings schema + ≤2K 硬上限截断
- Skill 绑模型 + context fork 解析
"""

import json
import os
import time

from src.agent.subagent import SubagentManager
from src.cost import CostLedger
from src.evaluation.evaluator import ContextMetrics, VerificationGate
from src.skills.loader import LazySkillLoader
from src.staging.sandbox import StagingSandbox


class TestCostLedger:
    def test_record_and_summary(self):
        """每 Op 记录 token 消耗/节省。"""
        ledger = CostLedger(warn_threshold=8000)
        ledger.record("op-1", "UserTurnStart", tokens_in=1000, tokens_out=200, tokens_saved=500)
        ledger.record("op-2", "ToolCall", tokens_in=2000, tokens_out=300, tokens_saved=1200)
        s = ledger.summary()
        assert s["total_ops"] == 2
        assert s["total_tokens_saved"] == 1700
        assert s["save_ratio"] > 0.4

    def test_high_cost_warning(self):
        """高成本操作预警。"""
        ledger = CostLedger(warn_threshold=8000)
        rec = ledger.record("op-h", "ToolCall", tokens_in=10000, tokens_out=2000)
        assert rec.warn is not None
        assert "高成本操作" in rec.warn
        assert len(ledger.high_cost_ops()) == 1

    def test_by_op_type(self):
        ledger = CostLedger()
        ledger.record("1", "ToolCall", tokens_in=100, tokens_out=10)
        ledger.record("2", "ToolCall", tokens_in=200, tokens_out=20)
        ledger.record("3", "Compact", tokens_in=50, tokens_out=5)
        agg = ledger.by_op_type()
        assert agg["ToolCall"]["count"] == 2
        assert agg["ToolCall"]["tokens"] == 330
        assert agg["Compact"]["count"] == 1

    def test_export(self):
        ledger = CostLedger()
        ledger.record("1", "UserTurnStart", tokens_in=10, tokens_out=2)
        exported = ledger.export()
        assert len(exported) == 1
        assert "op_type" in exported[0]


class TestVerificationGate:
    def test_passed_gate(self, tmp_path):
        """status=passed 才算完成。"""
        gate = VerificationGate(str(tmp_path))
        gate.write("t1", "passed")
        ok, msg = gate.is_passed("t1")
        assert ok is True
        assert msg == "passed"

    def test_fail_closed_missing_file(self, tmp_path):
        """fail-closed：无状态文件 = 未通过（绝不当通过）。"""
        gate = VerificationGate(str(tmp_path))
        ok, msg = gate.is_passed("t2")
        assert ok is False
        assert "fail-closed" in msg

    def test_fail_closed_non_passed_status(self, tmp_path):
        """status != passed → 未通过。"""
        gate = VerificationGate(str(tmp_path))
        gate.write("t3", "failed")
        ok, msg = gate.is_passed("t3")
        assert ok is False

    def test_stale_detection(self, tmp_path):
        """stale：输出比验证新 = 重验。"""
        gate = VerificationGate(str(tmp_path))
        gate.write("t4", "passed")
        # 模拟验证后文件被改（output_mtime > verified_at）
        future = time.time() + 100
        ok, msg = gate.is_passed("t4", output_mtime=future)
        assert ok is False
        assert "stale" in msg

    def test_write_failure_fail_closed(self, tmp_path):
        """写失败 → 当 failed（fail-closed 极端情况：workspace 路径是文件而非目录）。"""
        blocker = tmp_path / "blocker"
        blocker.write_text("", encoding="utf-8")
        gate = VerificationGate(str(blocker))  # workspace 指向一个文件 → makedirs 失败
        data = gate.write("t5", "passed")
        assert data["status"] == "failed"


class TestContextMetrics:
    def test_four_dimensions(self):
        """上下文四维指标（回捞/stale/遗漏/压缩比）。"""
        m = ContextMetrics()
        m.observe_assembly()
        m.observe_assembly(stale=True)
        m.observe_recall()
        m.observe_summary_miss()
        m.observe_compression(0.3)
        r = m.report()
        assert r["total_assembled"] == 2
        assert r["stale_hit_rate"] == 0.5
        assert r["recall_count"] == 1
        assert r["summary_misses"] == 1
        assert r["avg_compression_ratio"] == 0.3

    def test_zero_division_safe(self):
        m = ContextMetrics()
        r = m.report()
        assert r["stale_hit_rate"] == 0.0
        assert r["avg_compression_ratio"] == 0.0


class TestStagingAttestation:
    def test_attestation_generated(self, tmp_path):
        """stage 时生成 SHA256 attestation。"""
        sandbox = StagingSandbox()
        change = sandbox.stage("a.py", "old", "new")
        assert change.attestation is not None
        assert len(change.attestation) == 64  # SHA256 hex

    def test_tamper_detected(self, tmp_path):
        """篡改 = 拒绝 apply（审批后内容被改动）。"""
        sandbox = StagingSandbox()
        change = sandbox.stage("a.py", "old", "new")
        # 审批后内容被偷偷改动（模拟 Web 审批确认后篡改）
        change.new_content = "malicious"
        result = sandbox.apply(change.change_id)
        assert result["status"] == "tampered"
        assert "Attestation" in result["reason"]

    def test_normal_apply_with_attestation(self, tmp_path):
        """正常 apply（内容未被篡改）→ 通过。"""
        target = tmp_path / "a.py"
        target.write_text("old", encoding="utf-8")
        sandbox = StagingSandbox()
        change = sandbox.stage(str(target), "old", "new")
        result = sandbox.apply(change.change_id)
        assert result["status"] == "applied"
        assert target.read_text(encoding="utf-8") == "new"


class TestSubagentProtocol:
    def test_findings_extracted(self):
        """返回协议化：结构化 findings 提取（file/line/issue/severity/next_step）。"""
        def runner(prompt):
            return {
                "findings": [
                    {"file": "src/a.py", "line": 42, "issue": "空指针风险", "severity": "error", "next_step": "加判空"},
                    {"file": "src/b.py", "line": 7, "issue": "命名不清", "severity": "warn", "next_step": "重命名"},
                ]
            }

        mgr = SubagentManager(runner=runner)
        task = mgr.dispatch("审查代码")
        mgr.run(task)
        assert len(task.findings) == 2
        assert task.findings[0].file == "src/a.py"
        assert task.findings[0].severity == "error"

    def test_cap_enforced(self):
        """≤2K 硬上限：超限截断。"""
        from src.agent.subagent import FINDINGS_MAX_CHARS

        mgr = SubagentManager()
        big = "x" * 3000
        task = mgr.dispatch("t")
        task.result = {"findings": [{"issue": big}, {"issue": "small"}]}
        result = mgr._enforce_cap(task.result)
        assert len(result["findings"]) <= 1
        assert result.get("_truncated") is True

    def test_collect_includes_findings(self):
        """collect 结论回流带结构化 findings + truncated 标记。"""
        def runner(prompt):
            return {"findings": [{"file": "a.py", "issue": "问题1", "severity": "error", "next_step": "修复"}]}

        mgr = SubagentManager(runner=runner)
        task = mgr.dispatch("审查")
        mgr.run(task)
        data = mgr.collect(task)
        assert data["findings"][0]["file"] == "a.py"
        assert data["truncated"] is False
        assert data["status"] == "succeeded"


class TestSkillModelFork:
    def _make_skill(self, tmp_path, header=""):
        """创建测试技能目录。"""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir(exist_ok=True)
        (skill_dir / "SKILL.md").write_text(header, encoding="utf-8")
        return tmp_path

    def test_parse_model_fork(self, tmp_path):
        """SKILL.md 头部 model + context: fork 解析。"""
        root = self._make_skill(
            tmp_path,
            "---\nname: my-skill\ndescription: 测试技能\nmodel: astron-code-latest\ncontext: fork\n---\n正文",
        )
        loader = LazySkillLoader(root)
        skill = loader.list_skills()[0]
        assert skill["model"] == "astron-code-latest"
        assert skill["context_fork"] is True

    def test_route_for(self, tmp_path):
        """路由层取技能绑定属性。"""
        root = self._make_skill(
            tmp_path,
            "---\ndescription: 测试\nmodel: fast-model\ncontext: fork\n---\n正文",
        )
        loader = LazySkillLoader(root)
        route = loader.route_for("my-skill")
        assert route == {"name": "my-skill", "model": "fast-model", "context_fork": True}

    def test_without_fork_default_false(self, tmp_path):
        """无 fork 标记 → 默认 False（不污染既有技能）。"""
        root = self._make_skill(tmp_path, "---\ndescription: 普通技能\n---\n正文")
        loader = LazySkillLoader(root)
        skill = loader.list_skills()[0]
        assert skill["model"] is None
        assert skill["context_fork"] is False
