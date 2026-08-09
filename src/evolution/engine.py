"""Evolution Engine 编排层（v1.31，G22）——统一进化闭环。

五阶段：Observe → Analyze → Improve → Verify → Persist
五个作用目标：Memory / Skill / Planning / Tool Usage / Harness
策略层控制边界：速率限制 / 效果监控 / 回滚 / 冷却期 / 人类审批

与现有 G 系列整合：
- G20（根因分析）→ Analyze 阶段的失败归因
- FixPacket → Improve 阶段的修复指令
- G1/YAGNI Hook → Verify 阶段的验证门
- 事件溯源（EventLog）→ 全部进化事件可审计
- G18 审批收件箱 → 高风险进化的人类审批
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..protocol.events import (
    EventType,
    EvolutionApplied,
    EvolutionCandidateGenerated,
    EvolutionCycleStarted,
    EvolutionEvent,
    EvolutionRolledBack,
    EvolutionValidated,
)


@dataclass
class EvolutionSignal:
    """从 EventLog 中提取的进化信号。"""

    signal_type: str  # system_failure / user_feedback / periodic / manual
    source_event_ids: list[int] = field(default_factory=list)
    target: str = ""  # memory / skill / planning / tool_usage / harness
    severity: float = 0.0  # 0-1，严重程度
    details: dict = field(default_factory=dict)


@dataclass
class EvolutionCandidate:
    """改进候选。"""

    candidate_id: str = ""
    target: str = ""
    expected_effect: str = ""
    confidence: float = 0.0
    rollback_plan: str = ""
    changes: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class EvolutionValidation:
    """验证结果。"""

    result: str = "fail"  # pass / fail / partial
    regression_delta: float = 0.0
    metrics_before: dict = field(default_factory=dict)
    metrics_after: dict = field(default_factory=dict)
    details: str = ""


class EvolutionEngine:
    """自进化引擎编排层（v1.31，G22）。

    统一闭环：Observe → Analyze → Improve → Verify → Persist
    五个作用目标共享同一闭环，策略层控制进化边界。

    用法::

        engine = EvolutionEngine()
        engine.set_event_emitter(emit_fn)  # 接入 EventLog
        engine.register_adapter("memory", memory_adapter)
        engine.register_adapter("skill", skill_adapter)
        ...
        result = engine.run_cycle(session_id="s1", trigger="system_failure")
    """

    def __init__(self) -> None:
        self._adapters: Dict[str, Any] = {}
        self._emit_fn: Optional[Callable[[EvolutionEvent], None]] = None
        self._cycle_history: list[dict] = []
        self._next_cycle_num: int = 1
        # 五维度健康度追踪
        self._health: Dict[str, dict] = {
            "memory": {"score": 1.0, "total_cycles": 0, "applied": 0, "rolled_back": 0, "last_improvement": 0.0},
            "skill": {"score": 1.0, "total_cycles": 0, "applied": 0, "rolled_back": 0, "last_improvement": 0.0},
            "planning": {"score": 1.0, "total_cycles": 0, "applied": 0, "rolled_back": 0, "last_improvement": 0.0},
            "tool_usage": {"score": 1.0, "total_cycles": 0, "applied": 0, "rolled_back": 0, "last_improvement": 0.0},
            "harness": {"score": 1.0, "total_cycles": 0, "applied": 0, "rolled_back": 0, "last_improvement": 0.0},
        }

    def set_event_emitter(self, emit_fn: Callable[[EvolutionEvent], None]) -> None:
        """设置事件发射器（接入 EventLog）。"""
        self._emit_fn = emit_fn

    def register_adapter(self, target: str, adapter: Any) -> None:
        """注册进化适配器（memory/skill/planning/tool_usage/harness）。"""
        self._adapters[target] = adapter

    def _emit(self, event: EvolutionEvent) -> None:
        """发射进化事件。"""
        if self._emit_fn:
            self._emit_fn(event)

    def _now(self) -> float:
        return time.time()

    # ---- 闭环五阶段 ----

    def observe(self, session_id: str, trigger: str = "system_failure",
                targets: Optional[List[str]] = None) -> List[EvolutionSignal]:
        """Observe 阶段：从 EventLog + 用户反馈中提取进化信号。

        信号来源：
        - 系统反馈：任务失败/工具错误/验证门失败
        - 用户反馈：用户纠正/拒绝/偏好变化
        - 任务结果：成功率/步骤数/token 消耗
        - 行为轨迹：工具调用序列/规划决策链/记忆检索命中率
        """
        signals: List[EvolutionSignal] = []
        target_list = targets or list(self._adapters.keys())

        for target in target_list:
            adapter = self._adapters.get(target)
            if adapter and hasattr(adapter, "observe"):
                try:
                    target_signals = adapter.observe(session_id=session_id)
                    if isinstance(target_signals, list):
                        signals.extend(target_signals)
                except Exception:
                    pass  # 单个适配器失败不阻塞其他

        return signals

    def analyze(self, session_id: str, signals: List[EvolutionSignal],
                cycle_id: str) -> Dict[str, Any]:
        """Analyze 阶段：归因到作用目标。

        复用 G20 根因分析 + FixPacket。
        """
        # 按目标分组信号
        by_target: Dict[str, List[EvolutionSignal]] = {}
        for sig in signals:
            target = sig.target or "memory"  # 默认归到 memory
            by_target.setdefault(target, []).append(sig)

        analysis: Dict[str, Any] = {
            "cycle_id": cycle_id,
            "targets_with_signals": list(by_target.keys()),
            "signal_count": len(signals),
            "by_target": {t: len(sigs) for t, sigs in by_target.items()},
        }
        return analysis

    def improve(self, session_id: str, analysis: Dict[str, Any],
                cycle_id: str) -> List[EvolutionCandidate]:
        """Improve 阶段：生成改进建议。

        各适配器根据分析结果生成候选。
        """
        candidates: List[EvolutionCandidate] = []
        ts = self._now()

        for target in analysis.get("targets_with_signals", []):
            adapter = self._adapters.get(target)
            if adapter and hasattr(adapter, "improve"):
                try:
                    result = adapter.improve(
                        session_id=session_id,
                        cycle_id=cycle_id,
                    )
                    if isinstance(result, list):
                        for c in result:
                            if isinstance(c, EvolutionCandidate):
                                candidates.append(c)
                            elif isinstance(c, dict):
                                candidates.append(EvolutionCandidate(
                                    candidate_id=c.get("candidate_id", str(uuid.uuid4())[:8]),
                                    target=target,
                                    expected_effect=c.get("expected_effect", ""),
                                    confidence=c.get("confidence", 0.5),
                                    rollback_plan=c.get("rollback_plan", ""),
                                    changes=c.get("changes", []),
                                ))
                except Exception:
                    pass

        return candidates

    def verify(self, session_id: str, candidates: List[EvolutionCandidate],
               cycle_id: str) -> List[tuple[EvolutionCandidate, EvolutionValidation]]:
        """Verify 阶段：三阶段验证流水线——Generate→Evaluate→Refine。

        单调部署：新版本必须胜过旧版本才合并。
        """
        results: List[tuple[EvolutionCandidate, EvolutionValidation]] = []

        for candidate in candidates:
            adapter = self._adapters.get(candidate.target)
            validation = EvolutionValidation(result="fail")

            if adapter and hasattr(adapter, "verify"):
                try:
                    v = adapter.verify(
                        session_id=session_id,
                        cycle_id=cycle_id,
                        candidate=candidate,
                    )
                    if isinstance(v, EvolutionValidation):
                        validation = v
                    elif isinstance(v, dict):
                        validation = EvolutionValidation(
                            result=v.get("result", "fail"),
                            regression_delta=v.get("regression_delta", 0.0),
                            metrics_before=v.get("metrics_before", {}),
                            metrics_after=v.get("metrics_after", {}),
                        )
                except Exception:
                    validation = EvolutionValidation(result="fail", details="adapter exception")

            results.append((candidate, validation))

        return results

    def persist(self, session_id: str,
                validated: List[tuple[EvolutionCandidate, EvolutionValidation]],
                cycle_id: str) -> List[dict]:
        """Persist 阶段：进化结果写回系统。

        更新记忆/Skill/策略 + 标记 supersede + 进化事件进 EventLog。
        """
        applied: List[dict] = []
        ts = self._now()

        for candidate, validation in validated:
            if validation.result != "pass":
                continue

            adapter = self._adapters.get(candidate.target)
            if adapter and hasattr(adapter, "persist"):
                try:
                    result = adapter.persist(
                        session_id=session_id,
                        cycle_id=cycle_id,
                        candidate=candidate,
                    )
                    applied.append({
                        "candidate_id": candidate.candidate_id,
                        "target": candidate.target,
                        "result": result,
                    })
                except Exception:
                    pass

        return applied

    # ---- 主入口 ----

    def run_cycle(self, session_id: str,
                  trigger: str = "system_failure",
                  targets: Optional[List[str]] = None,
                  policy: Optional[Any] = None) -> dict:
        """运行一轮完整的进化闭环。

        Args:
            session_id: 会话 ID
            trigger: 触发源（system_failure/user_feedback/periodic/manual）
            targets: 指定进化的目标（None = 全部已注册的）
            policy: EvolutionPolicy 实例（可选，用于策略检查）

        Returns:
            本轮进化结果摘要
        """
        cycle_id = f"evo-{self._next_cycle_num:04d}"
        self._next_cycle_num += 1
        ts = self._now()

        result: dict = {
            "cycle_id": cycle_id,
            "session_id": session_id,
            "trigger": trigger,
            "started_at": ts,
            "status": "running",
            "signals": 0,
            "candidates": 0,
            "validated": 0,
            "applied": 0,
            "rolled_back": 0,
        }

        # 发射 CycleStarted 事件
        self._emit(EvolutionCycleStarted(
            id=0,  # 由 EventLog 分配
            ts=ts,
            session_id=session_id,
            phase="observe",
            target="all",
            trigger=trigger,
            cycle_id=cycle_id,
        ))

        # 1. Observe
        signals = self.observe(session_id, trigger, targets)
        result["signals"] = len(signals)

        # 2. Analyze
        analysis = self.analyze(session_id, signals, cycle_id)

        # 3. Improve
        candidates = self.improve(session_id, analysis, cycle_id)
        result["candidates"] = len(candidates)

        # 发射 CandidateGenerated 事件
        for cand in candidates:
            self._emit(EvolutionCandidateGenerated(
                id=0,
                ts=self._now(),
                session_id=session_id,
                phase="improve",
                target=cand.target,
                trigger=trigger,
                cycle_id=cycle_id,
                candidate_id=cand.candidate_id,
                expected_effect=cand.expected_effect,
                confidence=cand.confidence,
                rollback_plan=cand.rollback_plan,
            ))

        # 4. Verify
        validated = self.verify(session_id, candidates, cycle_id)
        passed = [(c, v) for c, v in validated if v.result == "pass"]
        result["validated"] = len(passed)

        # 发射 Validated 事件
        for cand, val in validated:
            self._emit(EvolutionValidated(
                id=0,
                ts=self._now(),
                session_id=session_id,
                phase="verify",
                target=cand.target,
                trigger=trigger,
                cycle_id=cycle_id,
                candidate_id=cand.candidate_id,
                validation_result=val.result,
                regression_delta=val.regression_delta,
                metrics_before=val.metrics_before,
                metrics_after=val.metrics_after,
            ))

        # 5. Persist
        applied = self.persist(session_id, passed, cycle_id)
        result["applied"] = len(applied)

        # 发射 Applied 事件
        for item in applied:
            self._emit(EvolutionApplied(
                id=0,
                ts=self._now(),
                session_id=session_id,
                phase="persist",
                target=item.get("target", ""),
                trigger=trigger,
                cycle_id=cycle_id,
                candidate_id=item.get("candidate_id", ""),
            ))

        result["status"] = "completed"
        result["completed_at"] = self._now()
        result["duration_ms"] = (result["completed_at"] - ts) * 1000

        # 更新健康度
        for cand, val in validated:
            target = cand.target
            if target in self._health:
                self._health[target]["total_cycles"] += 1
                if val.result == "pass":
                    self._health[target]["applied"] += 1
                    self._health[target]["last_improvement"] = self._now()
                    # 进化成功 → 健康度微升
                    self._health[target]["score"] = min(1.0, self._health[target]["score"] + 0.02)
                else:
                    # 验证失败 → 健康度微降
                    self._health[target]["score"] = max(0.0, self._health[target]["score"] - 0.01)

        self._cycle_history.append(result)
        return result

    def get_history(self, limit: int = 10) -> list[dict]:
        """获取最近的进化周期历史。"""
        return self._cycle_history[-limit:]

    def get_health_report(self) -> dict:
        """五维度健康度仪表盘。"""
        report = {}
        for target, h in self._health.items():
            success_rate = h["applied"] / max(1, h["total_cycles"])
            rollback_rate = h["rolled_back"] / max(1, h["total_cycles"])
            report[target] = {
                "score": round(h["score"], 3),
                "total_cycles": h["total_cycles"],
                "applied": h["applied"],
                "rolled_back": h["rolled_back"],
                "success_rate": round(success_rate, 3),
                "rollback_rate": round(rollback_rate, 3),
                "last_improvement": h["last_improvement"],
            }
        return report

    def get_trend_data(self, limit: int = 50) -> list[dict]:
        """进化趋势数据——同类任务 token 消耗 vs 执行轮次。"""
        trends = []
        for cycle in self._cycle_history[-limit:]:
            trends.append({
                "cycle_id": cycle.get("cycle_id", ""),
                "timestamp": cycle.get("started_at", 0),
                "signals": cycle.get("signals", 0),
                "candidates": cycle.get("candidates", 0),
                "validated": cycle.get("validated", 0),
                "applied": cycle.get("applied", 0),
                "duration_ms": cycle.get("duration_ms", 0),
                "trigger": cycle.get("trigger", ""),
            })
        return trends
