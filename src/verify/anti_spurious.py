"""反虚假相关（G12 v1.16 落地，对标 CAMEL arXiv:2605.09330 + ground-truth-only）。

设计（design.md G12）：
- 成功解法捕获区分"必要条件"（任务全周期执行、与失败重试无关的步骤）与"伴随事件"
  （一次性/被回滚/失败后废弃），后者降级 agent_inferred 不参与解法注入
- 检索注入前做确定性扰动测试（移除候选步骤，复用计数变化=因果必要保留，否则降权）
- 记忆条目必须携带 provenance 事件 ID，无事件证据的步骤不得进入"解法"字段
  （ground truth only：验证是同一轮的现场见证，不是事后补记）

范式声明：业务逻辑层 OOP + 纯函数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SolutionStep:
    """解法候选步骤。"""

    step_id: str
    description: str
    provenance_event_ids: list[int] = field(default_factory=list)  # 事件证据（ground truth）
    attribution: str = "agent_inferred"  # agent_inferred / verified
    reuse_count: int = 0  # 复用计数（因果信号）
    is_necessary: bool = False  # 必要条件（保留注入）


class AntiSpurious:
    """反虚假相关：必要条件 vs 伴随事件区分 + 扰动测试。

    原则：
    - ground-truth-only：无 provenance 事件证据的步骤不得进入"解法"（不参与注入）
    - 扰动测试：移除候选步骤后复用计数变化 → 因果必要保留；不变 → 伴随事件降权
    """

    def __init__(self) -> None:
        self._steps: dict[str, SolutionStep] = {}

    def add_step(self, step_id: str, description: str, provenance_event_ids: list[int]) -> SolutionStep:
        """添加候选步骤（带事件证据）。无证据 → 不参与注入（ground truth only）。"""
        step = SolutionStep(step_id=step_id, description=description, provenance_event_ids=provenance_event_ids)
        if not provenance_event_ids:
            step.attribution = "agent_inferred"  # 无证据：降级，不参与注入
        self._steps[step_id] = step
        return step

    def record_usage(self, step_id: str, *, outcome: str) -> None:
        """记录一次使用结果（成功/失败）——只更新复用计数。

        is_necessary（因果必要）由 perturb_test（扰动测试）判定——移除候选后复用
        计数变化才是因果信号，success 本身不代表必要（可能只是伴随）。
        """
        step = self._steps.get(step_id)
        if step is None:
            return
        if outcome == "success":
            step.reuse_count += 1
        elif outcome == "failure":
            step.reuse_count -= 1
            if step.reuse_count < 0:
                step.is_necessary = False

    def perturb_test(self, step_id: str, *, reuse_without: int) -> dict:
        """扰动测试：移除候选步骤后复用计数变化。

        复用计数变化大 → 因果必要保留；不变 → 伴随事件降权。
        """
        step = self._steps.get(step_id)
        if step is None:
            return {"step_id": step_id, "decision": "unknown", "reason": "step 不存在"}
        delta = step.reuse_count - reuse_without
        if delta >= 1:
            step.is_necessary = True
            decision = "keep"  # 移除后复用下降 → 因果必要
            reason = f"移除后复用 -{delta}"
        elif delta <= -1:
            step.is_necessary = False
            decision = "demote"  # 移除后复用上升 → 反因果
            reason = f"移除后复用 +{abs(delta)}"
        else:
            decision = "review"  # 无显著变化 → 伴随事件（观察）
            reason = "移除后复用无显著变化"
        return {"step_id": step_id, "decision": decision, "reason": reason, "reuse_count": step.reuse_count, "reuse_without": reuse_without}

    def injectable_steps(self) -> list[SolutionStep]:
        """可注入的解法步骤（必要条件 + 有事件证据 + 复用成功）。"""
        return [
            s for s in self._steps.values()
            if s.is_necessary and s.provenance_event_ids and s.reuse_count > 0
        ]

    def demoted_steps(self) -> list[SolutionStep]:
        """降级步骤（伴随事件/无证据——不参与解法注入）。"""
        return [s for s in self._steps.values() if not (s.is_necessary and s.provenance_event_ids and s.reuse_count > 0)]
