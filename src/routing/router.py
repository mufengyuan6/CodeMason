"""双模型路由。

- architect 强推理模型（规划）/ editor 快吞吐模型（执行）
- 路由：Plan 模式用强模型，Act 模式用快模型
- 同角色内降级：architect 挂 → 次强模型；editor 挂 → 最便宜模型；不跨角色
- Provider 抽象之上：路由决定角色，降级决定角色内选择
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..providers import BaseProvider, ProviderError


@dataclass
class ModelSpec:
    """模型规格。"""

    name: str
    role: str  # architect / editor
    priority: int = field(default=10)  # 越小越优先（降级链）
    fallback: list[str] = field(default_factory=list)  # 同角色降级链


@dataclass
class RoutingDecision:
    role: str
    model: str
    reason: str


class ModelRouter:
    """双模型路由：按模式选角色 → 角色内按优先级选模型（带降级链）。"""

    def __init__(
        self,
        provider: Optional[BaseProvider] = None,
        models: Optional[list[ModelSpec]] = None,
    ) -> None:
        # 兼容旧 API：不传 provider 时用 Mock（T6 重写旧 REST 后移除）
        if provider is None:
            from ..providers import MockProvider

            provider = MockProvider(reply="")
        self.provider = provider
        # 默认模型配置：architect 强推理 + editor 快吞吐
        self.models = models or [
            ModelSpec(name="deepseek-v4-flash", role="architect", priority=1, fallback=[]),
            ModelSpec(name="deepseek-v4-flash", role="editor", priority=1, fallback=[]),
        ]

    def route(self, mode: str = "act") -> RoutingDecision:
        """路由决策：Plan → architect，Act → editor。"""
        role = "architect" if mode == "plan" else "editor"
        return RoutingDecision(role=role, model=self._pick_model(role), reason=f"mode={mode} → role={role}")

    def route_role(self, role: str) -> RoutingDecision:
        """按角色直接路由（v1.27 新增：vision 子代理等自定义角色）。

        设计（能力接缝 G16）：architect/editor 之外的角色（如 vision）由
        调用方显式指定——模型 schema 恒定，只换实现层 provider。
        """
        model = self._pick_model(role)
        return RoutingDecision(role=role, model=model, reason=f"explicit role={role} → model={model}")

    def generate_role(self, messages: list[dict], *, role: str) -> str:
        """按指定角色调用 Provider（v1.27 新增：视觉子代理走 vision 角色）。

        与 generate() 的差异：角色由调用方显式给出（ReadImage 工具/Subagent
        委派），不经过 mode → role 映射——vision 模型不被 Plan/Act 主链路影响。
        """
        role_models = sorted(
            [m for m in self.models if m.role == role],
            key=lambda m: m.priority,
        )
        last_error: Optional[Exception] = None
        for spec in role_models:
            for model_name in [spec.name] + spec.fallback:
                try:
                    return self.provider.generate(messages, role=role, model=model_name)
                except ProviderError as e:
                    last_error = e
                    continue
        raise ProviderError(f"角色 {role} 全部模型降级失败: {last_error}")

    def generate(self, messages: list[dict], *, mode: str = "act") -> str:
        """按模式路由并调用 Provider（带同角色降级）。"""
        decision = self.route(mode)
        role_models = sorted(
            [m for m in self.models if m.role == decision.role],
            key=lambda m: m.priority,
        )
        last_error: Optional[Exception] = None
        for spec in role_models:
            for model_name in [spec.name] + spec.fallback:
                try:
                    return self.provider.generate(messages, role=decision.role, model=model_name)
                except ProviderError as e:
                    last_error = e
                    continue
        raise ProviderError(f"角色 {decision.role} 全部模型降级失败: {last_error}")

    def _pick_model(self, role: str) -> str:
        role_models = sorted([m for m in self.models if m.role == role], key=lambda m: m.priority)
        return role_models[0].name if role_models else "unknown"

    def get_cost_estimate(self, mode: str = "act") -> dict:
        """成本估算（占位，Phase 6 接入真实计价）。"""
        decision = self.route(mode)
        return {"role": decision.role, "model": decision.model, "note": "成本统计在 Phase 6 看板"}

    def get_routing_stats(self) -> dict:
        return {"models": [{"name": m.name, "role": m.role, "priority": m.priority} for m in self.models]}
