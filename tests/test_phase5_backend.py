"""Phase 5 测试：YAGNI 独立静态分析 Hook + Lazy Skills + 统一 Hook 框架。"""

import pytest

from src.constraints import YagniEngine
from src.harness import (
    HookContext,
    HookEvent,
    HookResult,
    UnifiedHooksManager,
    YagniValidationHook,
)
from src.skills import LazySkillLoader


class TestYagniEngine:
    def test_duplicate_detection(self):
        """L2：AST 相似度查重复。"""
        engine = YagniEngine()
        code = """\nfor i in range(5):\n    print(i)\nfor j in range(5):\n    print(j)\nfor k in range(5):\n    print(k)\n"""
        report = engine.validate("", code, "a.py")
        assert report.duplicates_found >= 1

    def test_stdlib_substitution(self):
        """L3：标准库替代检出。"""
        engine = YagniEngine()
        code = "import numpy\nx = numpy.mean([1,2,3])\n"
        report = engine.validate("", code, "a.py")
        messages = [f.message for f in report.findings]
        assert any("numpy.mean" in m for m in messages)

    def test_unused_deps(self):
        """L5：未使用依赖。"""
        engine = YagniEngine()
        code = "import requests\nimport os\nprint(os.getcwd())\n"
        report = engine.validate("", code, "a.py")
        messages = [f.message for f in report.findings]
        assert any("requests" in m for m in messages)

    def test_cyclomatic_complexity_block(self):
        """可读性守门：高圈复杂度 → block。"""
        engine = YagniEngine(max_complexity=3)
        code = """def complex_fn(x):\n    if x > 0:\n        if x > 1:\n            if x > 2:\n                if x > 3:\n                    return 1\n    return 0\n"""
        report = engine.validate("", code, "a.py")
        assert report.blocked is True
        assert report.readability_ok is False

    def test_lines_reduced(self):
        """G2：行数减少量化。"""
        engine = YagniEngine()
        old = "\n".join(f"# comment {i}" for i in range(20)) + "\ncode = 1\n"
        new = "code = 1\n"
        report = engine.validate(old, new, "a.py")
        assert report.lines_reduced == 20

    def test_four_dimension_report(self):
        """四维量化报告完整性。"""
        engine = YagniEngine()
        report = engine.validate("old\nold\n", "new\nnew\n", "a.py")
        d = report.to_dict()
        assert set(d.keys()) == {"lines_reduced", "deps_added", "duplicates_found", "readability_ok", "blocked", "findings"}

    def test_as_hook(self):
        """转 StagingSandbox Hook（G11 链路）。"""
        engine = YagniEngine()
        hook = engine.as_hook()
        from src.staging import StagingSandbox

        # 合法的深层嵌套（每层 if 有 body）
        lines = ["def complex_fn(x):"]
        for i in range(20):
            lines.append("    " * (i + 1) + f"if x > {i}:")
        lines.append("    " * 21 + "return 1")
        lines.append("    return 0")
        sb = StagingSandbox(hooks=[hook])
        sb.stage("a.py", "old\n" * 50, "\n".join(lines))
        change = sb.pending_changes()[0]
        result = sb.apply(change.change_id)
        # 复杂度超限 → blocked
        assert result["status"] == "blocked"


class TestUnifiedHooks:
    def test_register_and_run(self):
        mgr = UnifiedHooksManager()
        mgr.register_fn(HookEvent.TOOL_CALL, lambda ctx: HookResult("check", True, "ok"), name="precheck")
        ctx = HookContext(event=HookEvent.TOOL_CALL, tool_name="Bash", args={"command": "ls"})
        results = mgr.run(HookEvent.TOOL_CALL, ctx)
        assert results[0].allowed is True

    def test_block_semantics(self):
        mgr = UnifiedHooksManager()
        mgr.register_fn(HookEvent.TOOL_CALL, lambda ctx: HookResult("blocker", False, "危险", "block"), name="blocker")
        ctx = HookContext(event=HookEvent.TOOL_CALL, tool_name="Bash", args={"command": "rm -rf /"})
        results = mgr.run(HookEvent.TOOL_CALL, ctx)
        blocked = mgr.is_blocked(results)
        assert blocked is not None
        assert blocked.severity == "block"

    def test_priority_order(self):
        mgr = UnifiedHooksManager()
        order = []

        def make(name, priority):
            def fn(ctx):
                order.append(name)
                return HookResult(name, True, "ok")

            mgr.register_fn(HookEvent.EDIT, fn, name=name, priority=priority)

        make("low", __import__("src.harness", fromlist=["HookPriority"]).HookPriority.LOW)
        make("high", __import__("src.harness", fromlist=["HookPriority"]).HookPriority.HIGH)
        ctx = HookContext(event=HookEvent.EDIT)
        mgr.run(HookEvent.EDIT, ctx)
        assert order[0] == "high"

    def test_yagni_hook_blocks_staging(self):
        """G1 + G11：YAGNI Hook 作用 staging，block 拦截。"""
        from src.staging import StagingSandbox

        hook = YagniValidationHook()
        sb = StagingSandbox(hooks=[hook])
        # 高复杂度代码 → YAGNI block（合法深层嵌套）
        lines = ["def f(x):"]
        for i in range(20):
            lines.append("    " * (i + 1) + f"if x > {i}:")
        lines.append("    " * 21 + "return 1")
        lines.append("    return x")
        bad_code = "\n".join(lines)
        sb.stage("a.py", "def f(x):\n    return x\n", bad_code)
        change = sb.pending_changes()[0]
        result = sb.apply(change.change_id)
        assert result["status"] == "blocked"

    def test_yagni_hook_passes_good_code(self):
        from src.staging import StagingSandbox

        hook = YagniValidationHook()
        sb = StagingSandbox(hooks=[hook])
        good_code = "def add(a, b):\n    return a + b\n"
        sb.stage("a.py", "def add(a, b):\n    return a + b\n# comment\n", good_code)
        change = sb.pending_changes()[0]
        result = sb.apply(change.change_id)
        assert result["status"] == "applied"


class TestLazySkills:
    @pytest.fixture
    def skills_dir(self, tmp_path):
        d = tmp_path / "skills"
        (d / "refactor").mkdir(parents=True)
        (d / "refactor" / "SKILL.md").write_text("---\nname: refactor\ndescription: 代码重构技能\n---\n# Refactor\n步骤...\n", encoding="utf-8")
        (d / "debug").mkdir(parents=True)
        (d / "debug" / "SKILL.md").write_text("---\nname: debug\ndescription: 调试技能\n---\n# Debug\n方法...\n", encoding="utf-8")
        return d

    def test_list_skills(self, skills_dir):
        loader = LazySkillLoader(skills_dir)
        skills = loader.list_skills()
        assert len(skills) == 2
        names = {s["name"] for s in skills}
        assert names == {"refactor", "debug"}

    def test_prompt_injection_small(self, skills_dir):
        """注入 prompt <1000 token（未命中零开销）。"""
        loader = LazySkillLoader(skills_dir)
        prompt = loader.inject_prompt()
        assert len(prompt) < 1000
        assert "refactor" in prompt

    def test_load_stage2_content(self, skills_dir):
        loader = LazySkillLoader(skills_dir)
        result = loader.load("refactor", stage=2)
        assert result["stage"] >= 2
        assert "Refactor" in result["content"]

    def test_unknown_skill_none(self, skills_dir):
        loader = LazySkillLoader(skills_dir)
        assert loader.load("nonexistent") is None

    def test_zero_overhead_stats(self, skills_dir):
        """未命中零开销：只加载元数据不加载正文。"""
        loader = LazySkillLoader(skills_dir)
        stats = loader.stats()
        assert stats["total"] == 2
        assert stats["loaded"] == 0  # 未加载任何技能正文
        assert stats["zero_overhead"] == 2
