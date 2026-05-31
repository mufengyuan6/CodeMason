"""G13/G16 协议层测试：intent/clarify/planfile/steering（v1.23 落地）。"""

from src.protocol.clarify import (
    AmbiguityLevel,
    ClarificationOption,
    ClarificationRequested,
    should_clarify,
)
from src.protocol.intent import IntentDecompose, IntentType, SubIntent
from src.protocol.planfile import ImplementationStep, PlanStatus, SearchPlans
from src.protocol.steering import SteeringAck, SteeringCategory, SteeringMessage


class TestIntentDecompose:
    """多意图分解（G13）。"""

    def test_single_intent(self):
        d = IntentDecompose(intent_id="i1", intent_type=IntentType.SINGLE, user_message="修复登录 bug")
        assert d.is_composite is False
        assert d.subtask_count == 0
        assert d.model_dump_compact()["intent_type"] == "single"

    def test_composite_with_subtasks(self):
        d = IntentDecompose(
            intent_id="i2",
            intent_type=IntentType.COMPOSITE,
            user_message="修复登录 bug 并补充单元测试",
            subtasks=[
                SubIntent(subtask_id="s1", description="修复登录逻辑", requires_read=False),
                SubIntent(subtask_id="s2", description="补充登录测试", requires_read=True),
            ],
        )
        assert d.is_composite is True
        assert d.subtask_count == 2
        assert d.subtasks[0].requires_read is False  # 写子任务
        assert d.subtasks[1].priority == 1

    def test_ambiguous_hints(self):
        d = IntentDecompose(
            intent_id="i3", intent_type=IntentType.AMBIGUOUS,
            user_message="优化一下", ambiguity_hints=["缺少目标范围", "缺少验收标准"],
        )
        assert d.ambiguity_hints == ["缺少目标范围", "缺少验收标准"]
        assert d.confidence == 0.0


class TestClarify:
    """澄清策略（G13，防过度提问——Kimi K2.6 教训）。"""

    def test_none_never_asks(self):
        assert should_clarify(AmbiguityLevel.NONE) is False
        assert should_clarify(AmbiguityLevel.NONE, has_options=False) is False

    def test_low_never_asks(self):
        """低歧义可自行假设（防过度提问）。"""
        assert should_clarify(AmbiguityLevel.LOW) is False

    def test_medium_asks_with_options_only(self):
        """中歧义：有推荐选项才问。"""
        assert should_clarify(AmbiguityLevel.MEDIUM, has_options=True) is True
        assert should_clarify(AmbiguityLevel.MEDIUM, has_options=False) is False

    def test_high_always_asks(self):
        assert should_clarify(AmbiguityLevel.HIGH) is True

    def test_clarification_with_options(self):
        c = ClarificationRequested(
            clarification_id="c1",
            question="部署到哪个环境？",
            ambiguity_level=AmbiguityLevel.HIGH,
            options=[ClarificationOption(label="生产", description="生产环境"), ClarificationOption(label="测试")],
        )
        assert len(c.options) == 2
        assert c.model_dump_compact()["option_count"] == 2
        assert c.model_dump_compact()["ambiguity_level"] == "high"


class TestSearchPlans:
    """Search Plans 三支柱计划文件（G13/G17④）。"""

    def test_three_pillars(self):
        p = SearchPlans(
            plan_id="p1", task_id="t1",
            implementation_steps=[ImplementationStep(step_id="st1", description="加登录校验", verification="pytest test_login")],
            files_and_locations=["src/auth.py", "tests/test_login.py"],
            testing_and_validation=["pytest tests/test_login.py -q"],
        )
        assert p.status == PlanStatus.DRAFT
        assert len(p.implementation_steps) == 1
        assert len(p.files_and_locations) == 2
        assert len(p.testing_and_validation) == 1

    def test_freeze(self):
        p = SearchPlans(plan_id="p1", task_id="t1")
        f = p.freeze()
        assert f.status == PlanStatus.FROZEN
        # frozen 后验收断言跑过 → 产出 verified state（G17④ 衔接）
        assert f.model_dump_compact()["status"] == "frozen"


class TestSteering:
    """Steering 消息分级（G16⑤）。"""

    def test_categories(self):
        assert SteeringMessage(category=SteeringCategory.QUEUED, content="追加任务").category == SteeringCategory.QUEUED
        assert SteeringMessage(category=SteeringCategory.INJECT, content="补充上下文").category == SteeringCategory.INJECT
        assert SteeringMessage(category=SteeringCategory.STEERING, content="停，换方向").category == SteeringCategory.STEERING

    def test_ack_receipt(self):
        """回执确认模型在哪一步看到。"""
        ack = SteeringAck(message_id="m1", category=SteeringCategory.STEERING, seen_at="current_prompt", processed=True)
        assert ack.seen_at == "current_prompt"
        assert ack.model_dump_compact()["seen_at"] == "current_prompt"

    def test_queued_ack(self):
        ack = SteeringAck(message_id="m2", category=SteeringCategory.QUEUED, seen_at="queued", processed=False)
        assert ack.processed is False
