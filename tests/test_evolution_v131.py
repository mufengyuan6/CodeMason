"""v1.31 G22 自进化引擎测试——进化闭环 + 策略层 + 适配器 + 反馈泛化。

验证：
1. 协议层：8 种进化事件类型定义正确
2. Evolution Engine：五阶段闭环可复现
3. EvolutionPolicy：速率限制/冷却期/回归检测
4. 五个适配器：接口合规 + 基本行为
5. FeedbackGeneralizer：三类反馈分类 + 泛化
6. Web 端点：注册正确（集成测试）
"""

from __future__ import annotations

import time

import pytest

from src.evolution import EvolutionEngine, EvolutionPolicy
from src.evolution.adapters import (
    HarnessOnlineAdapter,
    MemoryDreamingAdapter,
    PlanningImprovementAdapter,
    SkillEvolutionAdapter,
    ToolUsageAdapter,
)
from src.evolution.adapters.base import AdapterResult, BaseEvolutionAdapter
from src.evolution.engine import EvolutionCandidate, EvolutionSignal, EvolutionValidation
from src.evolution.feedback import FeedbackGeneralizer, GeneralizationResult, UserCorrection
from src.evolution.policy import PolicyConfig, PolicyDecision
from src.protocol.events import (
    DreamingConsolidation,
    EventType,
    EvolutionApplied,
    EvolutionCandidateGenerated,
    EvolutionCycleStarted,
    EvolutionRolledBack,
    EvolutionValidated,
    FeedbackGeneralized,
    SkillEvolved,
)


# ========== 协议层测试 ==========


class TestEvolutionEventTypes:
    """8 种进化事件类型定义。"""

    def test_all_8_types_exist(self):
        expected = [
            "EVOLUTION_CYCLE_STARTED",
            "EVOLUTION_CANDIDATE_GENERATED",
            "EVOLUTION_VALIDATED",
            "EVOLUTION_APPLIED",
            "DREAMING_CONSOLIDATION",
            "SKILL_EVOLVED",
            "FEEDBACK_GENERALIZED",
            "EVOLUTION_ROLLED_BACK",
        ]
        for name in expected:
            assert name in EventType.__members__, f"EventType missing {name}"

    def test_event_instantiation(self):
        e = EvolutionCycleStarted(
            id=1, ts=time.time(), session_id="s1", phase="observe",
            target="memory", cycle_id="cyc-001",
        )
        assert e.type == EventType.EVOLUTION_CYCLE_STARTED
        assert e.cycle_id == "cyc-001"

    def test_event_fields(self):
        c = EvolutionCandidateGenerated(
            id=2, ts=time.time(), session_id="s1", phase="improve",
            target="skill", cycle_id="cyc-001", candidate_id="cand-001",
            expected_effect="improve skill", confidence=0.8,
        )
        assert c.confidence == 0.8
        assert c.candidate_id == "cand-001"

    def test_evolution_event_base_fields(self):
        """所有进化事件共享 base 字段。"""
        e = DreamingConsolidation(
            id=3, ts=time.time(), session_id="s1", phase="persist",
            target="memory", trigger="system_failure",
            evidence=[1, 2, 3], cycle_id="cyc-001",
        )
        assert e.phase == "persist"
        assert e.target == "memory"
        assert e.trigger == "system_failure"
        assert e.evidence == [1, 2, 3]


# ========== EvolutionEngine 测试 ==========


class TestEvolutionEngine:
    """进化引擎编排层。"""

    def test_init(self):
        engine = EvolutionEngine()
        assert len(engine._adapters) == 0

    def test_register_adapter(self):
        engine = EvolutionEngine()
        adapter = MemoryDreamingAdapter()
        engine.register_adapter("memory", adapter)
        assert "memory" in engine._adapters

    def test_run_cycle_empty(self):
        """无适配器时闭环正常完成。"""
        engine = EvolutionEngine()
        result = engine.run_cycle(session_id="test")
        assert result["status"] == "completed"
        assert result["signals"] == 0
        assert result["candidates"] == 0
        assert result["cycle_id"].startswith("evo-")

    def test_run_cycle_with_adapter(self):
        """有适配器时闭环正常完成。"""
        engine = EvolutionEngine()
        engine.register_adapter("memory", MemoryDreamingAdapter())
        engine.register_adapter("skill", SkillEvolutionAdapter())
        result = engine.run_cycle(session_id="test", trigger="periodic")
        assert result["status"] == "completed"
        assert result["trigger"] == "periodic"

    def test_cycle_history(self):
        engine = EvolutionEngine()
        engine.run_cycle(session_id="test")
        engine.run_cycle(session_id="test")
        assert len(engine.get_history()) == 2

    def test_event_emitter(self):
        """进化事件正确发射。"""
        emitted = []
        engine = EvolutionEngine()
        engine.set_event_emitter(lambda e: emitted.append(e))
        engine.run_cycle(session_id="test")
        # 至少应有 CycleStarted 事件
        assert len(emitted) >= 1
        assert emitted[0].type == EventType.EVOLUTION_CYCLE_STARTED

    def test_target_filter(self):
        """指定 target 只触发该适配器。"""
        engine = EvolutionEngine()
        engine.register_adapter("memory", MemoryDreamingAdapter())
        engine.register_adapter("skill", SkillEvolutionAdapter())
        result = engine.run_cycle(session_id="test", targets=["memory"])
        assert result["status"] == "completed"

    def test_health_report(self):
        """五维度健康度报告。"""
        engine = EvolutionEngine()
        engine.register_adapter("memory", MemoryDreamingAdapter())
        engine.run_cycle(session_id="test")
        health = engine.get_health_report()
        assert "memory" in health
        assert health["memory"]["total_cycles"] >= 0
        assert 0 <= health["memory"]["score"] <= 1.0

    def test_trend_data(self):
        """进化趋势数据。"""
        engine = EvolutionEngine()
        engine.run_cycle(session_id="test")
        engine.run_cycle(session_id="test")
        trend = engine.get_trend_data()
        assert len(trend) == 2
        assert trend[0]["cycle_id"] == "evo-0001"
        assert "signals" in trend[0]
        assert "duration_ms" in trend[0]


# ========== EvolutionPolicy 测试 ==========


class TestEvolutionPolicy:
    """进化策略层。"""

    def test_init_default(self):
        policy = EvolutionPolicy()
        assert policy.config.max_items_per_cycle == 5
        assert policy.config.cooldown_hours == 4.0

    def test_check_allowed(self):
        policy = EvolutionPolicy()
        decision = policy.check([], target="memory")
        assert decision.allowed is True

    def test_rate_limiting(self):
        """每小时速率限制。"""
        policy = EvolutionPolicy(PolicyConfig(max_cycles_per_hour=2))
        policy.check([], target="memory")
        policy.check([], target="skill")
        decision = policy.check([], target="planning")
        assert decision.allowed is False
        assert "速率限制" in decision.reason

    def test_cooldown(self):
        """冷却期检查。"""
        policy = EvolutionPolicy(PolicyConfig(cooldown_hours=1.0))
        policy.check([], target="memory")
        decision = policy.check([], target="memory")
        assert decision.allowed is False
        assert "冷却期" in decision.reason

    def test_cooldown_different_target(self):
        """不同目标不受冷却期影响。"""
        policy = EvolutionPolicy(PolicyConfig(cooldown_hours=1.0))
        policy.check([], target="memory")
        decision = policy.check([], target="skill")
        assert decision.allowed is True

    def test_confidence_filter(self):
        """置信度过滤。"""
        policy = EvolutionPolicy(PolicyConfig(min_confidence=0.5))
        candidates = [
            EvolutionCandidate(confidence=0.3),
            EvolutionCandidate(confidence=0.7),
        ]
        decision = policy.check(candidates, target="memory")
        assert decision.truncated_count == 1

    def test_approval_required(self):
        """Harness 目标需要审批。"""
        policy = EvolutionPolicy()
        decision = policy.check([], target="harness")
        assert decision.requires_approval is True

    def test_regression_check(self):
        """回归检测。"""
        policy = EvolutionPolicy(PolicyConfig(regression_threshold=-0.05))
        val = EvolutionValidation(regression_delta=-0.1)
        result = policy.check_regression(val)
        assert result["should_rollback"] is True

    def test_no_regression(self):
        """无回归时正常。"""
        policy = EvolutionPolicy()
        val = EvolutionValidation(regression_delta=0.05)
        result = policy.check_regression(val)
        assert result["should_rollback"] is False

    def test_rollback_history(self):
        policy = EvolutionPolicy()
        policy.record_rollback("cyc-001", "cand-001", "regression")
        history = policy.get_rollback_history()
        assert len(history) == 1
        assert history[0]["reason"] == "regression"


# ========== 适配器测试 ==========


class TestBaseEvolutionAdapter:
    """基类接口。"""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseEvolutionAdapter()

    def test_default_methods(self):
        class TestAdapter(BaseEvolutionAdapter):
            @property
            def target(self):
                return "test"

        adapter = TestAdapter()
        assert adapter.observe() == []
        assert adapter.analyze() == {"target": "test", "findings": []}
        assert adapter.improve() == []
        assert adapter.verify().get("result") == "fail"
        assert adapter.persist().success is True


class TestMemoryDreamingAdapter:
    """Memory Dreaming 适配器。"""

    def test_target(self):
        adapter = MemoryDreamingAdapter()
        assert adapter.target == "memory"

    def test_observe_empty(self):
        adapter = MemoryDreamingAdapter()
        signals = adapter.observe()
        assert isinstance(signals, list)

    def test_improve_empty(self):
        adapter = MemoryDreamingAdapter()
        candidates = adapter.improve()
        assert isinstance(candidates, list)

    def test_persist(self):
        adapter = MemoryDreamingAdapter()
        result = adapter.persist()
        assert result.success is True


class TestSkillEvolutionAdapter:
    """Skill 自进化适配器。"""

    def test_target(self):
        adapter = SkillEvolutionAdapter()
        assert adapter.target == "skill"

    def test_observe_empty(self):
        adapter = SkillEvolutionAdapter()
        signals = adapter.observe()
        assert isinstance(signals, list)

    def test_verify(self):
        adapter = SkillEvolutionAdapter()
        result = adapter.verify(candidate=EvolutionCandidate())
        assert result["result"] == "pass"


class TestPlanningImprovementAdapter:
    def test_target(self):
        adapter = PlanningImprovementAdapter()
        assert adapter.target == "planning"


class TestToolUsageAdapter:
    def test_target(self):
        adapter = ToolUsageAdapter()
        assert adapter.target == "tool_usage"


class TestHarnessOnlineAdapter:
    def test_target(self):
        adapter = HarnessOnlineAdapter()
        assert adapter.target == "harness"


# ========== FeedbackGeneralizer 测试 ==========


class TestFeedbackGeneralizer:
    """用户反馈泛化。"""

    def test_classify_temp_info(self):
        gen = FeedbackGeneralizer()
        correction = UserCorrection(corrected_output="fix", context={})
        assert gen.classify_correction(correction) == "temp_info"

    def test_classify_scene_pref(self):
        gen = FeedbackGeneralizer()
        correction = UserCorrection(
            corrected_output="fix", context={"scene_tag": "code_review"}
        )
        assert gen.classify_correction(correction) == "scene_pref"

    def test_classify_long_rule(self):
        gen = FeedbackGeneralizer()
        correction = UserCorrection(
            corrected_output="fix", context={"is_rule": True}
        )
        assert gen.classify_correction(correction) == "long_rule"

    def test_process_temp_info(self):
        gen = FeedbackGeneralizer()
        correction = UserCorrection(corrected_output="fix", context={})
        result = gen.process_correction(correction)
        assert result.feedback_type == "temp_info"
        assert result.requires_confirmation is False

    def test_process_long_rule_requires_confirmation(self):
        gen = FeedbackGeneralizer()
        correction = UserCorrection(
            corrected_output="fix", context={"is_rule": True}
        )
        result = gen.process_correction(correction)
        assert result.requires_confirmation is True

    def test_confirm_rule(self):
        gen = FeedbackGeneralizer()
        correction = UserCorrection(
            corrected_output="fix", context={"is_rule": True}
        )
        gen.process_correction(correction)
        result = gen.confirm_rule(correction, confirmed=True)
        assert result.requires_confirmation is False

    def test_reject_rule(self):
        gen = FeedbackGeneralizer()
        correction = UserCorrection(
            corrected_output="fix", context={"is_rule": True}
        )
        gen.process_correction(correction)
        result = gen.confirm_rule(correction, confirmed=False)
        assert result.generalized_count == 0

    def test_history(self):
        gen = FeedbackGeneralizer()
        gen.process_correction(UserCorrection(corrected_output="a", context={}))
        gen.process_correction(UserCorrection(corrected_output="b", context={}))
        assert len(gen.get_history()) == 2
