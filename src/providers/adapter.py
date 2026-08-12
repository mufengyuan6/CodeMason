"""ModelRouterAdapter：真实 LLM → AgentLoop 协议适配层（v1.30，T-11 真实主链路接入）。

背景：
- AgentLoop 通过 LLMProtocol 接口消费 LLM（generate(messages, *, role=editor)）
- ModelRouter 承载真实双 provider（architect/editor=DeepSeek，vision=MiMo 独立实例故障域隔离）
- 此前主链路从未接入真实 LLM——CLI 用 MockLLM、server 裸 LOOP（Agent 空转）

本适配器把 ModelRouter 适配成 LLMProtocol：
- generate(messages, role=...) → router.generate_role(messages, role)（角色透传）
- role=vision → 自动走独立视觉 provider（MiMo，故障域隔离，主链路崩不影响看图）
- 角色缺失 → 按 mode 默认映射（architect=plan / editor=act，兼容旧调用）
- 无凭据/构建失败 → 降级 MockProvider（fail-safe，不阻断内核启动）

范式声明：适配器层 OOP（薄适配，无业务逻辑）。
"""

from __future__ import annotations

from typing import Optional

from ..routing.router import ModelRouter, RoutingDecision

# 角色 → 默认 mode 映射（无 ModelRouter 时的兜底语义）
ROLE_TO_MODE = {"architect": "plan", "editor": "act", "vision": "act"}


class ModelRouterAdapter:
    """把 ModelRouter 适配成 AgentLoop 的 LLMProtocol 接口。

    用法：
        router = build_router_from_credentials()
        loop = AgentLoop(event_log=log, llm=ModelRouterAdapter(router), ...)
    """

    def __init__(self, router: Optional[ModelRouter] = None, *, fallback_reply: str = "") -> None:
        self.router = router
        self._fallback_reply = fallback_reply
        # 调用台账（与 BaseProvider.calls 同构：成本归因/审计消费）
        self.calls: list[dict] = []

    # ---------- LLMProtocol 接口 ----------

    def generate(self, messages: list[dict], *, role: str = "editor") -> str:
        """按角色调用真实 LLM（LLMProtocol 签名）。

        - router 就绪 → generate_role（vision 走独立 MiMo provider）
        - router 缺失 → 降级 Mock（fail-safe）
        """
        if self.router is None:
            self.calls.append({"role": role, "provider": "mock-fallback", "messages": messages})
            return self._fallback_reply
        try:
            # 成功后才记录真实 provider 调用（失败记录 error，calls[0] 语义一致）
            result = self.router.generate_role(messages, role=role)
            self.calls.append({"role": role, "provider": self.router.provider.config.name, "messages": messages})
            return result
        except Exception as e:
            # 真实 LLM 失败 → 降级兜底（不阻断内核；错误由调用方/重试层处理）
            self.calls.append({"role": role, "provider": "error", "error": str(e)[:200]})
            return self._fallback_reply

    # ---------- 路由查询（供驾驶舱/审计展示） ----------

    def route(self, mode: str = "act") -> RoutingDecision:
        """路由决策（透传 ModelRouter；缺失时返回兜底决策）。"""
        if self.router is None:
            return RoutingDecision(role="editor" if mode == "act" else "architect", model="mock", reason="mock fallback")
        return self.router.route(mode)

    def get_routing_stats(self) -> dict:
        if self.router is None:
            return {"models": [{"name": "mock", "role": "editor", "priority": 1}]}
        return self.router.get_routing_stats()

    # ---------- 生命周期 ----------

    def close(self) -> None:
        """关闭底层 provider（释放 httpx 连接）。"""
        if self.router is not None:
            try:
                self.router.provider.close()
            except Exception:
                pass
            try:
                vision = getattr(self.router, "_vision_provider", None)
                if vision is not None:
                    vision.close()
            except Exception:
                pass


def build_adapter_from_credentials() -> ModelRouterAdapter:
    """从凭据通道构建真实 LLM 适配器（生产主链路）。

    构建失败（缺凭据/配置错误）→ 返回 Mock 兜底适配器（fail-safe，内核照常启动）。
    """
    from ..routing.router import build_router_from_credentials

    try:
        router = build_router_from_credentials()
        return ModelRouterAdapter(router=router)
    except Exception as e:
        # fail-safe：无凭据时内核不崩，降级 Mock（Web 驾驶舱仍可演示状态机）
        return ModelRouterAdapter(router=None, fallback_reply=f"[mock-fallback] 真实 LLM 不可用: {e}")
