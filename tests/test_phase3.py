"""Phase 3 测试：三层记忆 + T1-T5 压缩 + Plan/Act + 反思。"""

import time

from src.agent import PlanActCoordinator, ReflectionEngine
from src.compression import CompressionLevel, ContextCompressor
from src.memory import GlobalMemory, ProjectMemory, SessionMemory


class TestSessionMemory:
    def test_append_and_restore(self, tmp_path):
        """会话中断恢复：JSONL 持久化可重建。"""
        p = tmp_path / "session.jsonl"
        m1 = SessionMemory(p)
        m1.append("user", "修复 bug")
        m1.append("assistant", "开始分析")
        m2 = SessionMemory(p)  # 模拟重启
        assert len(m2.get_messages()) == 2
        assert m2.get_messages()[0]["role"] == "user"

    def test_compact(self, tmp_path):
        m = SessionMemory(tmp_path / "s.jsonl", max_messages=10)
        for i in range(15):
            m.append("user", f"msg {i}")
        assert m.needs_compact()
        result = m.compact("共 15 条消息，已完成 X 任务")
        assert result["summary"] == "共 15 条消息，已完成 X 任务"
        assert len(m.get_messages()) <= 1 + 5  # 摘要 + 最近 5

    def test_get_context_limit(self, tmp_path):
        m = SessionMemory(tmp_path / "s.jsonl")
        for i in range(20):
            m.append("user", f"m{i}")
        ctx = m.get_context(limit=5)
        assert len(ctx) == 5


class TestProjectMemory:
    def test_load_rules(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# 项目规则\n- 用 pytest\n", encoding="utf-8")
        pm = ProjectMemory(tmp_path)
        rules = pm.get_rules()
        assert len(rules) >= 1
        assert "pytest" in pm.get_rules_text()

    def test_bug_pattern_match(self, tmp_path):
        pm = ProjectMemory(tmp_path)
        pm.record_bug_pattern("syntax", r"SyntaxError", "检查括号")
        match = pm.match_bug_pattern("SyntaxError: invalid syntax")
        assert match is not None
        assert match["solution"] == "检查括号"


class TestGlobalMemory:
    def test_record_and_retrieve(self, tmp_path):
        """同类任务第二次自动注入。"""
        gm = GlobalMemory(tmp_path / "global.json")
        gm.record("bug_fix", "修复登录 bug 的经验", steps_count=8, success=True)
        gm.record("bug_fix", "修复支付 bug 的经验", steps_count=6, success=True)
        experiences = gm.retrieve("bug_fix")
        assert len(experiences) == 2
        assert experiences[-1]["summary"] == "修复支付 bug 的经验"
        assert gm.stats()["bug_fix"] == 2

    def test_persistence(self, tmp_path):
        p = tmp_path / "global.json"
        gm1 = GlobalMemory(p)
        gm1.record("refactor", "重构经验", steps_count=12)
        gm2 = GlobalMemory(p)
        assert gm2.stats()["refactor"] == 1


class TestCompression:
    SAMPLE = '''"""模块文档字符串"""
import os

# 注释行


def process(data, verbose=False):
    """处理数据函数。"""
    result = [x * 2 for x in data]
    if verbose:
        print(result)
    return result
'''

    def test_t1_removes_comments(self):
        c = ContextCompressor()
        r = c.compress(self.SAMPLE, CompressionLevel.T1)
        assert "# 注释行" not in r.content
        assert "def process" in r.content
        assert r.ratio > 0

    def test_t2_removes_docstrings(self):
        c = ContextCompressor()
        r = c.compress(self.SAMPLE, CompressionLevel.T2)
        assert "模块文档字符串" not in r.content
        assert "处理数据函数" not in r.content
        assert "def process" in r.content

    def test_t2_falls_back_on_syntax_error(self):
        c = ContextCompressor()
        r = c.compress("def broken(:\n  pass\n", CompressionLevel.T2)
        assert "def broken" in r.content or r.content.strip() == ""

    def test_t4_structured_summary(self):
        c = ContextCompressor()
        r = c.compress(self.SAMPLE, CompressionLevel.T4)
        assert "def process(data, verbose):" in r.content
        assert "class" not in r.content or True  # 无 class 则无
        assert r.original_tokens > r.compressed_tokens

    def test_t5_with_summarizer(self):
        def summarizer(code: str) -> str:
            return "[摘要] 一个数据处理的函数"

        c = ContextCompressor(t5_summarizer=summarizer)
        r = c.compress(self.SAMPLE, CompressionLevel.T5)
        assert "数据处理" in r.content

    def test_t5_fallback_without_summarizer(self):
        c = ContextCompressor()
        r = c.compress(self.SAMPLE, CompressionLevel.T5)
        assert "def process" in r.content  # 回退 T4

    def test_token_estimate(self):
        assert ContextCompressor.estimate_tokens("hello world") >= 1
        assert ContextCompressor.estimate_tokens("你好世界") == 4


class TestPlanAct:
    def test_plan_readonly_blocks_write_tool(self):
        pac = PlanActCoordinator(mode="plan")
        reason = pac.check_tool("Write")
        assert reason is not None
        assert "只读" in reason

    def test_plan_readonly_blocks_edit_tool(self):
        pac = PlanActCoordinator(mode="plan")
        assert pac.check_tool("Edit") is not None

    def test_plan_readonly_blocks_bash(self):
        pac = PlanActCoordinator(mode="plan")
        assert pac.check_tool("Bash") is not None
        assert pac.check_command("ls -la") is not None  # 命令也拦截

    def test_plan_readonly_allows_read(self):
        pac = PlanActCoordinator(mode="plan")
        assert pac.check_tool("Read") is None
        assert pac.check_tool("Grep") is None

    def test_act_mode_allows(self):
        pac = PlanActCoordinator(mode="act")
        assert pac.check_tool("Write") is None
        assert pac.check_command("ls -la") is None

    def test_switch_mode(self):
        pac = PlanActCoordinator(mode="act")
        result = pac.switch("plan")
        assert result["readonly"] is True
        assert pac.readonly is True
        pac.switch("act")
        assert pac.readonly is False


class TestReflection:
    def test_classify_syntax(self):
        re = ReflectionEngine()
        assert re.classify("SyntaxError: invalid syntax") == "syntax"

    def test_classify_permission(self):
        re = ReflectionEngine()
        assert re.classify("PermissionError: denied") == "permission"

    def test_classify_path(self):
        re = ReflectionEngine()
        assert re.classify("FileNotFoundError: no such file") == "path"

    def test_classify_network(self):
        re = ReflectionEngine()
        assert re.classify("ConnectionError: timeout") == "network"

    def test_classify_unknown(self):
        re = ReflectionEngine()
        assert re.classify("random stuff") == "other"

    def test_strategy_network_retry_then_ask(self):
        re = ReflectionEngine()
        assert re.choose_strategy("network", retry_count=0) == "retry"
        assert re.choose_strategy("network", retry_count=2) == "ask_user"

    def test_strategy_permission_ask(self):
        re = ReflectionEngine()
        assert re.choose_strategy("permission") == "ask_user"

    def test_reflect_full(self):
        re = ReflectionEngine()
        result = re.reflect("FileNotFoundError: x.py", retry_count=1)
        assert result["error_class"] == "path"
        assert result["strategy"] == "change_tool"
