"""v1.13 上下文压缩插件化测试（T-E，P0）。

验收标准（design 3.2 阶段4/5 + 强制测试要求）：
- 压缩即事件：Condensation 进 EventLog，审计链完整可重放
- Session Guide 快照：压缩后凭 ≤2KB 快照恢复，任务/决策/错误不丢
- pinned facts 豁免：user_confirmed 事实压缩后仍在，不可物理删
- 压缩质量 gate：压缩后抽查关键信息保留
- condenser 注册表 + 管道组合器 + 预算感知短路
- 概率性遗忘：指数衰减软删除
- 事件回读 + 压缩遗漏信号（re-fetch 率 = 压缩过度信号）
"""

import json

from src.context import (
    AB_POLICIES,
    CONDENSER_REGISTRY,
    CompressionManager,
    DEFAULT_PIPELINE,
    EventRecallService,
    PipeComposer,
)
from src.context.condensers import ObservationMask, ProbabilisticForgetting, SignatureSummary


def _sample_events(n: int = 20, intent: str = "修复登录 bug") -> list[dict]:
    events = []
    for i in range(1, n + 1):
        if i % 5 == 0:
            events.append({"id": i, "type": "Error", "content": {"description": f"错误 {i}"}})
        elif i % 3 == 0:
            events.append({"id": i, "type": "ItemCompleted", "content": {"path": f"src/mod{i}.py", "summary": f"修改文件 {i}"}})
        else:
            events.append({"id": i, "type": "AgentMessageContentDelta", "content": f"delta {i}"})
    return events


class TestCondenserRegistry:
    def test_registry_has_builtins(self):
        """注册表包含全部内置 condenser。"""
        for name in ("comment_strip", "signature_summary", "observation_mask", "tool_clearing", "probabilistic_forgetting", "llm_summary"):
            assert name in CONDENSER_REGISTRY, f"缺 {name}"

    def test_pipe_composer_default(self):
        """默认管道：串联执行 + 压缩比。"""
        composer = PipeComposer(DEFAULT_PIPELINE)
        text = "# 注释\n\ndef process(data, verbose=False):\n    # 内部注释\n    return [x * 2 for x in data]\n"
        out, results = composer.run(text)
        assert len(results) >= 1
        assert len(out) < len(text)
        assert composer.describe()["version"].startswith("pipe-")

    def test_budget_short_circuit(self):
        """预算感知短路：便宜方案达标即跳过剩余。"""
        composer = PipeComposer(["comment_strip", "llm_summary"], budget_tokens=10)
        text = "# a\n# b\n# c\n\nsimple"
        out, results = composer.run(text)
        # 短路后不执行 llm_summary
        assert not any(r.key == "llm_summary" for r in results) or len(out) <= 10

    def test_observation_mask(self):
        """占位符结构：超阈值观察替换为 [Observation masked: N chars]。"""
        c = ObservationMask()
        long_text = "x" * 2000
        r = c.condense(long_text)
        assert "masked" in r.output
        assert "[Observation masked: 2,000 chars]" == r.output

    def test_probabilistic_forgetting(self):
        """概率性遗忘：指数衰减软删除，旧事件被清除。"""
        c = ProbabilisticForgetting()
        lines = "\n".join(f"event {i}" for i in range(50))
        r = c.condense(lines)
        # 新事件保留、极旧事件被清除
        assert "event 49" in r.output
        assert "event 0" not in r.output

    def test_signature_summary(self):
        """T4 结构化摘要：保留接口隐藏实现。"""
        c = SignatureSummary()
        code = "def process(data, verbose=False):\n    return [x*2 for x in data]"
        r = c.condense(code)
        assert "def process(data, verbose):" in r.output


class TestCompressionManager:
    def test_trigger_threshold(self):
        """触发判断：60% 工作上限触发（Chroma 2026）。"""
        cm = CompressionManager(trigger_at=120000)
        assert cm.should_compress(120000) is True
        assert cm.should_compress(1000) is False

    def test_compress_produces_condensation_event(self, tmp_path):
        """压缩即事件：Condensation 事件生成，审计链完整。"""
        cm = CompressionManager(policy="default")
        events = _sample_events(30)
        result = cm.compress(events, session_id="s1", intent="修复登录 bug")
        ev = result["condensation_event"]
        assert ev.policy_version.startswith("pipe-")
        assert ev.first_event_id == 1
        assert ev.last_event_id == 30
        assert ev.tokens_before > ev.tokens_after
        assert result["verified"] is True
        # 摘要含事件 ID 引用（可回读）
        assert "[e" in result["summary"]

    def test_session_guide_recovery(self):
        """Session Guide：压缩后凭 ≤2KB 快照恢复，任务/决策不丢。"""
        cm = CompressionManager()
        events = _sample_events(20, intent="修复登录 bug")
        result = cm.compress(events, session_id="s1", intent="修复登录 bug")
        guide_md = result["guide_markdown"]
        # ≤2KB 约束
        assert len(guide_md.encode("utf-8")) <= 2048
        assert "Session Guide" in guide_md
        assert "Intent" in guide_md
        assert "修复登录 bug" in guide_md
        # 关键态（任务/文件）保留
        assert "Files Modified" in guide_md or "tasks" in guide_md.lower()

    def test_pinned_facts_exempt(self):
        """pinned facts 豁免：user_confirmed 事实永不丢弃。"""
        cm = CompressionManager()
        events = _sample_events(10)
        pinned = ["构建命令: pytest", "测试框架: pytest"]
        result = cm.compress(events, pinned_facts=pinned, session_id="s1")
        assert result["pinned_protected"] == pinned

    def test_quality_gate_verify_llm(self):
        """质量 gate：verify_llm 失败则 verified=False。"""
        cm = CompressionManager(verify_llm=lambda text: False)
        events = _sample_events(10)
        result = cm.compress(events, session_id="s1")
        assert result["verified"] is False

    def test_ab_policies_exist(self):
        """condenser A/B 对照：策略版本化（压缩策略对照评测基础设施）。"""
        assert "default" in AB_POLICIES
        assert "aggressive_forgetting" in AB_POLICIES
        assert "gentle" in AB_POLICIES


class TestEventRecall:
    def test_recall_read_and_stats(self, tmp_path):
        """事件回读 + 统计（re-fetch 率）。"""
        from src.storage import EventLog
        from src.protocol import TurnStarted

        el = EventLog(tmp_path / "s.jsonl")
        el.append(TurnStarted(id=1, session_id="s", mode="act", turn_index=1, op_id="o", ts=1.0))
        svc = EventRecallService(event_log=el)
        ev = svc.read(1)
        assert ev is not None
        assert svc.stats.total_reads == 1

    def test_omission_signal(self):
        """压缩遗漏信号：回读压缩区域 → re-fetch 率上升 → 压缩过度。"""
        svc = EventRecallService(event_log=None)
        svc.mark_compressed_recall(5)
        svc.mark_compressed_recall(6)
        report = svc.omission_report()
        assert report["compressed_reads"] == 2
        assert report["refetch_rate"] == 1.0
        assert "调低" in report["action"]


class TestTimeMachine:
    """v1.13 核心：视图时间旅行 view(event_id, policy)。"""

    def test_view_rebuilds_historical_snapshot(self):
        """view(event_id, policy)：任意历史时刻重建窗口视图。"""
        from src.context.timemachine import TimeMachine

        tm = TimeMachine()
        events = _sample_events(20, intent="修复登录 bug")
        snap = tm.view(10, policy="default", events_override=events, intent="修复登录 bug")
        assert snap.event_id == 10
        assert snap.policy == "default"
        # 只渲染截至 event 10
        assert snap.meta["events_rendered"] == 10
        assert all(e.get("id", 0) <= 10 for e in snap.recent_events)
        # 摘要含事件 ID 引用
        assert "[e" in snap.summary or snap.summary == ""

    def test_view_pure_function_same_input_same_output(self):
        """纯函数语义：同输入同输出（评测可复现）。"""
        from src.context.timemachine import TimeMachine

        tm1, tm2 = TimeMachine(), TimeMachine()
        events = _sample_events(15)
        s1 = tm1.view(8, "default", events_override=events).to_dict()
        s2 = tm2.view(8, "default", events_override=events).to_dict()
        assert s1["summary"] == s2["summary"]
        assert s1["recent_events"] == s2["recent_events"]

    def test_compare_policies_ab(self):
        """condenser A/B 对照：同事件流不同策略对比指标。"""
        from src.context.timemachine import TimeMachine

        tm = TimeMachine()
        events = _sample_events(30)
        result = tm.compare_policies(30, policies=["default", "aggressive_forgetting", "gentle"], events_override=events)
        assert set(result["comparison"][0].keys()) >= {"policy", "summary_chars", "ratio_vs_default"}
        assert len(result["snapshots"]) == 3
        # 每个策略快照可独立渲染
        assert result["snapshots"]["default"]["event_id"] == 30

    def test_restore_resume_from_event(self):
        """断点精确续接：恢复 = 重建目标时刻视图。"""
        from src.context.timemachine import TimeMachine

        tm = TimeMachine()
        events = _sample_events(18, intent="重构模块")
        restored = tm.restore(12, policy="default")
        assert restored["resume_from"] == 12
        assert "Session Guide" in restored["guide_markdown"]
        assert len(restored["recent_events"]) <= tm.keep_recent + 1  # 尾部最近 N 轮

    def test_reproduce_fault_at_moment(self):
        """故障复现：重建"出问题"时刻的视图。"""
        from src.context.timemachine import TimeMachine

        tm = TimeMachine()
        events = _sample_events(25)
        # 用户报"第 18 个事件后上下文出问题"
        snap = tm.view(18, policy="default", events_override=events)
        rendered_ids = [e.get("id") for e in snap.recent_events]
        assert all(i <= 18 for i in rendered_ids)
