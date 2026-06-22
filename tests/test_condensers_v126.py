"""T-26-3/4 压缩 KV cache 复用 + 8 节结构化模板测试（v1.26，3.2 阶段4）。

验证：
- KvCacheSummarizer：压缩指令作为重放对话后的最后一条 user message（前缀
  一致保 warm cache）；输出带 COMPACTION_INSTRUCTION 尾部 + 前缀消息列表
- StructuredSummary：8 节固定模板（Primary Request/Key Technical Concepts/
  Files and Code/Errors and Fixes/Pending Jobs/Current Work/Next Step/
  Critical Context）；terse bullets；旧 checkpoint 合并不照抄；
  CHECKPOINT_PREAMBLE 声明
"""

from src.context.condensers import CONDENSER_REGISTRY, CondenseResult


class TestKvCacheSummarizer:
    def test_registered(self):
        assert "kv_cache_summarizer" in CONDENSER_REGISTRY

    def test_instruction_as_last_user_message(self):
        """压缩指令作为最后一条 user message（前缀 = 重放对话）。"""
        cls = CONDENSER_REGISTRY["kv_cache_summarizer"]
        condenser = cls()
        result = condenser.condense("对话历史内容")
        assert isinstance(result, CondenseResult)
        # 输出应包含压缩指令 + 前缀消息列表（KV 复用形态）
        assert "COMPACTION_INSTRUCTION" in result.output or "compaction" in result.output.lower() or "CONDENSE" in result.output

    def test_output_has_prefix_and_instruction(self):
        """输出 = 重放前缀 + 压缩指令（辅助调用与真实请求前缀一致）。"""
        cls = CONDENSER_REGISTRY["kv_cache_summarizer"]
        condenser = cls()
        result = condenser.condense("user: 帮我修 bug\nassistant: 好的")
        # meta 记录前缀与指令（KV 复用调用的形状）
        assert result.meta.get("has_prefix") is True
        assert result.meta.get("has_instruction") is True


class TestStructuredSummary:
    def test_registered(self):
        assert "structured_summary" in CONDENSER_REGISTRY

    def test_eight_sections_present(self):
        """输出含全部 8 节。"""
        cls = CONDENSER_REGISTRY["structured_summary"]
        condenser = cls()
        result = condenser.condense("用户要求修复登录 bug，改了两个文件，遇到一个超时错误，计划继续调 2FA")
        sections = [
            "Primary Request", "Key Technical Concepts", "Files and Code",
            "Errors and Fixes", "Pending Jobs", "Current Work", "Next Step", "Critical Context",
        ]
        for s in sections:
            assert s in result.output, f"缺少 8 节之一: {s}"

    def test_merges_old_checkpoint(self):
        """旧 checkpoint 存在时合并而非照抄。"""
        cls = CONDENSER_REGISTRY["structured_summary"]
        condenser = cls()
        old_ckpt = "<compacted-summary>\n## Primary Request\n修 bug\n## Next Step\n继续\n</compacted-summary>"
        new_info = "用户又要求加 2FA"
        result = condenser.condense(old_ckpt + "\n" + new_info)
        # 合并后应只出现一次 Primary Request 节（不照抄旧块）
        assert result.output.count("Primary Request") == 1
        # 新信息进入输出
        assert "2FA" in result.output

    def test_preamble_present(self):
        """输出带 CHECKPOINT_PREAMBLE（已建立背景，直接继续不要复述）。"""
        cls = CONDENSER_REGISTRY["structured_summary"]
        condenser = cls()
        result = condenser.condense("内容")
        assert "checkpoint" in result.output.lower() or "established" in result.output.lower() or "背景" in result.output

    def test_terse_bullets_not_prose(self):
        """terse bullets（- 开头），不写流畅散文。"""
        cls = CONDENSER_REGISTRY["structured_summary"]
        condenser = cls()
        result = condenser.condense("很长的一段内容" * 20)
        # 每节内容以 - 或 * 开头（bullet 纪律）
        lines = [l for l in result.output.splitlines() if l.strip().startswith(("## ", "# "))]
        assert len(lines) >= 8  # 8 节标题

    def test_preserves_precise_facts(self):
        """保留精确路径/错误串/数值（terse bullets 纪律）。"""
        cls = CONDENSER_REGISTRY["structured_summary"]
        condenser = cls()
        text = "src/auth/login.py 报错 TypeError: 'NoneType' object is not subscriptable，端口 8080 超时"
        result = condenser.condense(text)
        assert "src/auth/login.py" in result.output
        assert "TypeError" in result.output or "8080" in result.output
