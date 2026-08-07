"""v1.28 G20 ②测试：LLM 归因假设（agent_inferred 永不升级 + Doubt-driven 证伪 + 降级）。

对应 design.md G20 II + ADR-05（agent_inferred 永不自动升级）。
"""

import json
import time

import pytest

from src.projection.attribution import AttributionEngine
from src.projection.root_cause import FailureChain
from src.projection.root_cause_analyzer import AttributionHypothesis
from src.protocol import ItemCompleted
from src.providers.base import MockProvider


def _chain(**kw) -> FailureChain:
    base = dict(
        session_id="s1",
        anchor_event_id=3,
        failures=[
            {"id": 3, "type": "Error", "message": "SyntaxError: unexpected indent", "failure_stage": "edit", "related_tool": "Edit"}
        ],
        related_events=[
            {"id": 2, "kind": "tool_result", "tool_name": "Edit"},
            {"id": 4, "kind": "approval", "tool_name": "Bash"},
        ],
        trace_records=[],
        yagni_findings=[],
        fix_packets=[],
    )
    base.update(kw)
    return FailureChain(**base)


def _provider_returning(payload: str):
    """构造按调用次数返回不同内容的 provider（归因 + 证伪两阶段）。"""

    class ScriptedProvider(MockProvider):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def generate(self, messages, *, role="editor", model=None):
            self.calls += 1
            return payload

    return ScriptedProvider()


class TestAttributionEngine:
    """归因假设：LLM 生成 + agent_inferred 硬标记 + Doubt-driven 证伪。"""

    def test_attribute_returns_hypotheses(self):
        provider = _provider_returning(
            json.dumps(
                {
                    "hypotheses": [
                        {"hypothesis": "缩进错误导致语法失败", "confidence": 0.85, "evidence_ref": ["3"]},
                        {"hypothesis": "编辑工具写入了错误内容", "confidence": 0.4, "evidence_ref": ["2"]},
                    ]
                },
                ensure_ascii=False,
            )
        )
        engine = AttributionEngine(provider)
        hyps = engine.attribute(_chain())
        assert len(hyps) == 2
        assert hyps[0].hypothesis == "缩进错误导致语法失败"
        assert hyps[1].confidence == pytest.approx(0.4)

    def test_agent_inferred_always_true(self):
        """ADR-05：LLM 产出归因永远 agent_inferred（不可升级为事实）。"""
        provider = _provider_returning(
            json.dumps({"hypotheses": [{"hypothesis": "h1", "confidence": 0.9, "evidence_ref": []}]})
        )
        engine = AttributionEngine(provider, doubt_check=False)
        hyps = engine.attribute(_chain())
        assert all(h.agent_inferred is True for h in hyps)

    def test_agent_inferred_flag_in_dict(self):
        provider = _provider_returning(
            json.dumps({"hypotheses": [{"hypothesis": "h1", "confidence": 0.8, "evidence_ref": ["3"]}]})
        )
        engine = AttributionEngine(provider, doubt_check=False)
        hyps = engine.attribute(_chain())
        assert hyps[0].to_dict()["agent_inferred"] is True

    def test_doubt_rejected_removes_top(self):
        """Doubt-driven 证伪：top 假设被 rejected → 剔除（第二候选顶上来）。"""
        provider = _provider_returning(
            json.dumps({"hypotheses": [
                {"hypothesis": "候选A", "confidence": 0.9, "evidence_ref": []},
                {"hypothesis": "候选B", "confidence": 0.7, "evidence_ref": []},
            ]})
        )
        engine = AttributionEngine(provider, doubt_check=True)
        # 证伪阶段返回 rejected
        provider.payload2 = json.dumps({"verdict": "rejected", "reason": "无证据", "alternative": "候选B"})

        class TwoPhase(provider.__class__):
            def generate(self, messages, *, role="editor", model=None):
                if self.calls >= 1:
                    return provider.payload2
                return super().generate(messages, role=role, model=model)

        engine._provider = TwoPhase()
        hyps = engine.attribute(_chain())
        assert all(h.hypothesis != "候选A" for h in hyps)  # top 被剔除

    def test_doubt_doubtful_lowers_confidence(self):
        provider = _provider_returning(
            json.dumps({"hypotheses": [{"hypothesis": "h", "confidence": 0.9, "evidence_ref": []}]})
        )
        engine = AttributionEngine(provider, doubt_check=True)
        engine._provider = _TwoPhaseProvider(engine._provider, json.dumps({"verdict": "doubtful", "reason": "证据弱"}))
        hyps = engine.attribute(_chain())
        assert hyps[0].confidence < 0.9  # 降权 0.5

    def test_no_provider_returns_empty(self):
        """无 provider → 空归因（分析器 status=degraded 纯确定性）。"""
        engine = AttributionEngine(None)
        assert engine.attribute(_chain()) == []
        assert engine.stats["attributions"] == 0

    def test_llm_failure_degrades_empty(self):
        class BoomProvider(MockProvider):
            def generate(self, messages, *, role="editor", model=None):
                raise RuntimeError("llm down")

        engine = AttributionEngine(BoomProvider())
        hyps = engine.attribute(_chain())
        assert hyps == []
        assert engine.stats["llm_failures"] == 1

    def test_noisy_output_tolerated(self):
        """LLM 输出带 JSON 外噪 → 提取首个 JSON 对象。"""
        noisy = "思考中……\n" + json.dumps({"hypotheses": [{"hypothesis": "h", "confidence": 0.5, "evidence_ref": []}]}) + "\n完毕"
        provider = _provider_returning(noisy)
        engine = AttributionEngine(provider, doubt_check=False)
        hyps = engine.attribute(_chain())
        assert len(hyps) == 1 and hyps[0].hypothesis == "h"

    def test_invalid_json_fallback_parse(self):
        provider = _provider_returning('不能解析 {"hypothesis": "从失败文本推断"} 的内容')
        engine = AttributionEngine(provider, doubt_check=False)
        hyps = engine.attribute(_chain())
        assert any("从失败文本推断" in h.hypothesis for h in hyps)

    def test_statistics_tracked(self):
        provider = _provider_returning(
            json.dumps({"hypotheses": [{"hypothesis": "h", "confidence": 0.6, "evidence_ref": []}]})
        )
        engine = AttributionEngine(provider, doubt_check=False)
        engine.attribute(_chain())
        assert engine.stats["attributions"] == 1


class _TwoPhaseProvider(MockProvider):
    """归因阶段用第一 provider 结果，证伪阶段返回指定 payload。"""

    def __init__(self, first, second_payload):
        super().__init__()
        self.first = first
        self.second = second_payload
        self.calls = 0

    def generate(self, messages, *, role="editor", model=None):
        self.calls += 1
        if self.calls == 1:
            return self.first.generate(messages, role=role, model=model)
        return self.second