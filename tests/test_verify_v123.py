"""v1.16 验证确定性测试：phantom_edit/fix_packet/fact_checker/fact_preservation/anti_spurious/lookup_before_fetch。

验收（design.md Phase 1 强制测试）：
- 变更级验证门：SHA256 phantom-edit 检测（声称改了但 checksum 没变=拦截）
- FixPacket：机器可读失败契约（violation+verification.commands+constraints）
- fact-checker：三态判定（VERIFIED/WRONG/UNVERIFIABLE）+ 不接受主会话声称当证据
- 事实保全：五态 + 保全率（零 LLM）
- 反虚假相关：必要条件/伴随事件区分 + 扰动测试 + ground-truth-only
- lookup-before-fetch：资源取用前验证存在（供应链安全）
"""

import pytest

from src.verify.anti_spurious import AntiSpurious
from src.verify.fact_checker import FactChecker
from src.verify.fact_preservation import compare_preservation, extract_facts
from src.verify.fix_packet import FixPacketBuilder, Violation
from src.verify.lookup_before_fetch import LookupBeforeFetch
from src.verify.phantom_edit import PhantomEditDetector


class TestPhantomEditDetector:
    """变更级验证门（G11）。"""

    def test_real_change_detected(self, tmp_path):
        """内容真的变了 → changed=True，phantom=False。"""
        f = tmp_path / "a.py"
        f.write_text("old content")
        det = PhantomEditDetector()
        det.snapshot_before(str(f))
        f.write_text("new content changed")
        result = det.verify_change(str(f))
        assert result["changed"] is True
        assert result["phantom"] is False

    def test_phantom_edit_blocked(self, tmp_path):
        """声称改了但 checksum 没变 → phantom=True（拦截）。"""
        f = tmp_path / "b.py"
        f.write_text("same content")
        det = PhantomEditDetector()
        det.snapshot_before(str(f))
        f.write_text("same content")  # 没变
        result = det.verify_change(str(f))
        assert result["phantom"] is True

    def test_content_level_phantom(self):
        """内容级：声称的"新内容"与"旧内容"相同 = 假变更。"""
        det = PhantomEditDetector()
        r = det.verify_content_change(claimed_old="print(1)", actual_old="print(1)", new_content="print(1)")
        assert r["phantom"] is True  # 新旧相同

    def test_stale_base_detected(self):
        """基于过期旧内容 → stale_base（防"照着旧文件改"）。"""
        det = PhantomEditDetector()
        r = det.verify_content_change(claimed_old="old version", actual_old="newer version", new_content="result")
        assert r["stale_base"] is True


class TestFixPacket:
    """FixPacket 机器可读失败契约（G11）。"""

    def test_packet_structure(self):
        builder = FixPacketBuilder()
        packet = builder.build(
            stage="staging_apply",
            violations=[Violation(code="YAGNI_001", file="src/x.py", line=10, end_line=12, message="重复实现", hint="用标准库")],
            instructions=["删除重复代码"],
            verification_commands=["python -m pytest tests/test_x.py -q"],
            constraints={"allowed_scope": ["src/x.py"], "no_new_deps": True},
        )
        assert packet.has_p0() is True
        d = packet.to_dict()
        assert d["stage"] == "staging_apply"
        assert d["violations"][0]["file"] == "src/x.py"
        assert d["verification_commands"] == ["python -m pytest tests/test_x.py -q"]
        assert d["constraints"]["no_new_deps"] is True

    def test_from_verify_failure(self):
        packet = FixPacketBuilder.from_verify_failure(
            stage="gate", file="src/a.py", message="语法错误", line=5,
            verification_commands=["python -m py_compile src/a.py"],
        )
        assert packet.status == "failed"
        assert packet.violations[0].line == 5
        assert "py_compile" in packet.verification_commands[0]

    def test_to_json(self):
        builder = FixPacketBuilder()
        packet = builder.build(stage="lint", violations=[Violation(code="L1", file="f.py", message="x")])
        import json

        assert json.loads(packet.to_json())["stage"] == "lint"


class TestFactChecker:
    """事实核查子代理（G15：三态判定，不接受主会话声称当证据）。"""

    def test_file_exists_verified(self, tmp_path):
        (tmp_path / "real.py").write_text("x")
        fc = FactChecker(project_root=str(tmp_path))
        r = fc.check_file_exists("存在 real.py", "real.py")
        assert r.status == "VERIFIED"

    def test_file_exists_wrong(self, tmp_path):
        fc = FactChecker(project_root=str(tmp_path))
        r = fc.check_file_exists("存在 ghost.py", "ghost.py")
        assert r.status == "WRONG"

    def test_contains_verified(self, tmp_path):
        (tmp_path / "auth.py").write_text("def login(): pass")
        fc = FactChecker(project_root=str(tmp_path))
        r = fc.check_contains("已实现 login", "auth.py", "def login")
        assert r.status == "VERIFIED"
        assert "auth.py:1" in r.evidence[0]  # file:line 引用

    def test_no_placeholders(self, tmp_path):
        (tmp_path / "done.py").write_text("def f():\n    return 42")
        fc = FactChecker(project_root=str(tmp_path))
        assert fc.check_no_placeholders("完成", "done.py").status == "VERIFIED"
        # 有 TODO → WRONG
        (tmp_path / "todo.py").write_text("# TODO: 未完成")
        assert fc.check_no_placeholders("完成", "todo.py").status == "WRONG"

    def test_scope_narrow(self, tmp_path):
        (tmp_path / "narrow.py").write_text("def f():\n    pass  # for now")
        fc = FactChecker(project_root=str(tmp_path))
        r = fc.check_scope_words("完成", "narrow.py")
        assert r.status == "WRONG"  # 收窄词检测


class TestFactPreservation:
    """事实保全五态校验（keepfacts：零 LLM）。"""

    ORIGINAL = "成本 $100，版本 v1.2.3，占比 45%，URL https://a.com，邮箱 a@b.com"
    SUMMARY_OK = "成本 $100，版本 v1.2.3，占比 45%"
    SUMMARY_CHANGED = "成本 $200，版本 v1.2.3"

    def test_extract_facts(self):
        facts = extract_facts("价格 ¥500，v2.1.0，50%，https://x.com")
        types = {f["type"] for f in facts}
        assert "money" in types
        assert "version" in types
        assert "percentage" in types
        assert "url" in types

    def test_preserved(self):
        result = compare_preservation(self.ORIGINAL, self.SUMMARY_OK)
        assert result["status"] == "ok"
        assert result["preserve_rate"] >= 0.6
        assert not result["invalid"]

    def test_changed_detected(self):
        result = compare_preservation(self.ORIGINAL, self.SUMMARY_CHANGED)
        # $100 → $200 被标记 changed 或 missing（同类型不同值）
        assert result["changed"] or result["missing"]

    def test_invalid_fact_in_summary(self):
        result = compare_preservation("成本 $100", "成本 $100，额外出现 999 用户")
        assert result["invalid"]  # 摘要出现但原文没有 → invalid

    def test_full_width_normalization(self):
        """NFKC 全角归一：全角数字与半角等价。"""
        facts = extract_facts("占比５０％")  # 全角
        assert any(f["type"] == "percentage" for f in facts)


class TestAntiSpurious:
    """反虚假相关（G12：必要条件/伴随事件 + 扰动测试 + ground-truth-only）。"""

    def test_no_provenance_demoted(self):
        """无事件证据 → 不参与注入（ground truth only）。"""
        asr = AntiSpurious()
        asr.add_step("s1", "执行 pytest", [])  # 无证据
        assert asr.injectable_steps() == []  # 不注入
        assert len(asr.demoted_steps()) == 1

    def test_necessary_kept(self):
        asr = AntiSpurious()
        asr.add_step("s1", "先跑测试", [1, 2, 3])  # 有事件证据
        asr.record_usage("s1", outcome="success")
        asr.record_usage("s1", outcome="success")
        # 扰动测试：移除后复用降 → 因果必要
        result = asr.perturb_test("s1", reuse_without=0)
        assert result["decision"] == "keep"
        assert len(asr.injectable_steps()) == 1

    def test_coincidental_demoted(self):
        """伴随事件：移除后复用无变化 → 降权。"""
        asr = AntiSpurious()
        asr.add_step("s2", "随机步骤", [4])
        asr.record_usage("s2", outcome="success")
        result = asr.perturb_test("s2", reuse_without=1)  # 移除后复用不变
        assert result["decision"] == "review"
        assert asr.injectable_steps() == []  # 伴随事件不注入


class TestLookupBeforeFetch:
    """lookup-before-fetch（供应链安全：HalluSquatting 防御）。"""

    def test_local_known_verified(self):
        lbf = LookupBeforeFetch(local_known=["requests", "fastapi"])
        v = lbf.verify("requests", "package")
        assert v.verified is True
        assert v.risk == "safe"
        assert lbf.must_fetch(v) is True

    def test_unknown_risky(self):
        lbf = LookupBeforeFetch(local_known=[])
        v = lbf.verify("hallucinated-pkg-xyz", "package")
        assert v.verified is False
        assert v.risk == "risky"  # 未验证 → 不取用（需人工确认）
        assert lbf.must_fetch(v) is False

    def test_registry_lookup(self):
        known = {"requests", "numpy"}

        def fake_registry(resource, rtype):
            return resource in known

        lbf = LookupBeforeFetch(registry_lookup=fake_registry)
        assert lbf.verify("requests", "package").verified is True
        assert lbf.verify("fake-pkg", "package").verified is False

    def test_url_format(self):
        lbf = LookupBeforeFetch()
        v = lbf.verify("https://pypi.org/project/requests/", "url")
        assert v.verified is True

    def test_invalid_format_blocked(self):
        lbf = LookupBeforeFetch()
        v = lbf.verify("rm -rf /", "package")  # 非法格式
        assert v.risk == "blocked"
        assert lbf.must_fetch(v) is False
