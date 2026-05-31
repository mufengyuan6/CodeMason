"""独立 judge 模型族路由（G14 v1.22 落地：防 Verifier Theater）。

设计（design.md G14）：
- 生成用 A 家模型、验证用 B 家模型（不同厂商）——模型自评 = 同一套权重同一套盲点
  （Fable 5 自检 "real but defeatable"）
- AC/DC 双环：probabilistic 自检内环（模型写测试自验）+ deterministic 分析外环
  （YAGNI 静态分析/G7 机读门禁——天然独立于生成模型）
- 4.1 双模型路由扩展一条 role=judge 规则：验证任务强制走不同 Provider

范式声明：业务逻辑层 OOP。
"""

from __future__ import annotations

from typing import Optional


class JudgeRouter:
    """独立 judge 路由：验证任务强制走与生成不同的 Provider。

    保证：judge_provider != generation_provider（不同厂商权重不同盲点）。
    """

    def __init__(self, generation_provider: str = "xfyun", judge_provider: Optional[str] = None) -> None:
        self.generation_provider = generation_provider
        self.judge_provider = judge_provider or self._pick_judge(generation_provider)

    @staticmethod
    def _pick_judge(gen_provider: str) -> str:
        """生成 A 家 → 验证 B 家（默认映射：讯飞生成 → 其他家验证）。"""
        mapping = {
            "xfyun": "deepseek",
            "deepseek": "xfyun",
            "openai": "anthropic",
            "anthropic": "openai",
        }
        return mapping.get(gen_provider, "deepseek")  # 未知生成商 → 默认 deepseek 验证

    def resolve(self, role: str, provider: Optional[str] = None) -> str:
        """按角色解析 provider：judge 强制独立于生成。"""
        if role == "judge":
            return self.judge_provider
        return provider or self.generation_provider

    def is_independent(self) -> bool:
        """judge 与生成是否独立（不同厂商）。"""
        return self.generation_provider != self.judge_provider
