"""按 Op 类型分派路由（4.1 v1.23 落地：双模型路由 P1 升级）。

设计（design.md PRD v1.23）：
- 按 Op 类型分派：read/format/简单修改走便宜模型，plan/refactor/复杂 bug 走强模型
- 路由合规审计（防软配置被绕过——OpenClaw 烧 $300/天教训：agent 可改配置绕过路由）
- 与 ModelRouter（模式级）互补：模式级定角色（architect/editor），Op 级定具体模型档
- 成本管控刚需：企业成本驾驶舱的数据底座

范式声明：业务逻辑层 OOP。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class OpTier(str, Enum):
    """Op 类型分档（v1.23：按计算需求分档）。"""

    CHEAP = "cheap"        # read/format/简单修改 → 便宜模型
    STANDARD = "standard"  # 常规任务 → 标准模型
    EXPENSIVE = "expensive"  # plan/refactor/复杂 bug → 强模型


@dataclass
class OpRoutingRule:
    """Op → 档位映射规则。"""

    op_pattern: str  # Op 类型/工具名（支持 * 通配）
    tier: OpTier
    priority: int = 10  # 越小越优先（精确匹配优先）


@dataclass
class OpRoutingDecision:
    """Op 分派决策。"""

    op_name: str
    tier: OpTier
    model_role: str  # architect / editor
    reason: str
    audit_id: str = ""


class OpRouter:
    """Op 类型分派器：工具/Op → 便宜/标准/强模型。

    默认映射（v1.23 PRD）：
    - cheap（editor）：Read/Glob/Grep/WebSearch/WebFetch/Monitor + 格式化类
    - standard（editor）：Write/Edit/AskUser
    - expensive（architect）：Bash 复杂命令/plan/refactor/subagent 委派
    """

    DEFAULT_RULES: list[OpRoutingRule] = [
        OpRoutingRule("Read", OpTier.CHEAP, priority=1),
        OpRoutingRule("Glob", OpTier.CHEAP, priority=1),
        OpRoutingRule("Grep", OpTier.CHEAP, priority=1),
        OpRoutingRule("WebSearch", OpTier.CHEAP, priority=1),
        OpRoutingRule("WebFetch", OpTier.CHEAP, priority=1),
        OpRoutingRule("Monitor", OpTier.CHEAP, priority=1),
        OpRoutingRule("Write", OpTier.STANDARD, priority=1),
        OpRoutingRule("Edit", OpTier.STANDARD, priority=1),
        OpRoutingRule("AskUser", OpTier.STANDARD, priority=1),
        OpRoutingRule("Bash", OpTier.EXPENSIVE, priority=1),
        OpRoutingRule("run_code", OpTier.EXPENSIVE, priority=1),
        OpRoutingRule("plan", OpTier.EXPENSIVE, priority=1),
        OpRoutingRule("refactor", OpTier.EXPENSIVE, priority=1),
        OpRoutingRule("subagent", OpTier.EXPENSIVE, priority=1),
        OpRoutingRule("*", OpTier.STANDARD, priority=99),  # 兜底
    ]

    def __init__(self, rules: Optional[list[OpRoutingRule]] = None) -> None:
        self._rules = sorted(rules or self.DEFAULT_RULES, key=lambda r: r.priority)
        self._audit: list[dict] = []
        self._seq = 0
        self._config_checksum: Optional[str] = None  # 路由合规审计基准

    def route(self, op_name: str) -> OpRoutingDecision:
        """按 Op 类型分派（精确匹配 → 通配 → 兜底）。"""
        import fnmatch

        for rule in self._rules:
            if fnmatch.fnmatch(op_name, rule.op_pattern):
                tier = rule.tier
                role = "architect" if tier == OpTier.EXPENSIVE else "editor"
                self._seq += 1
                decision = OpRoutingDecision(
                    op_name=op_name,
                    tier=tier,
                    model_role=role,
                    reason=f"op={op_name} → {tier.value} (rule={rule.op_pattern})",
                    audit_id=f"opr-{self._seq}",
                )
                self._audit.append({"audit_id": decision.audit_id, "op": op_name, "tier": tier.value, "role": role, "reason": decision.reason})
                return decision
        # 兜底（理论不可达：* 规则存在）
        return OpRoutingDecision(op_name=op_name, tier=OpTier.STANDARD, model_role="editor", reason="fallback")

    def compliance_check(self, *, current_role: str, expected_role: str, op_name: str) -> dict:
        """路由合规审计：防软配置被绕过（OpenClaw 教训）。

        场景：agent 修改配置/提示词试图绕过便宜模型分派 → 检测角色与期望不符。
        """
        ok = current_role == expected_role
        result = {"ok": ok, "op": op_name, "expected_role": expected_role, "current_role": current_role, "violation": not ok}
        self._audit.append({"audit_id": f"aud-{len(self._audit) + 1}", "op": op_name, "compliance": result})
        return result

    def audit(self, limit: int = 100) -> list[dict]:
        """路由审计日志（成本归因 + 合规）。"""
        return self._audit[-limit:]

    def stats(self) -> dict:
        """路由统计（成本驾驶舱数据）。"""
        by_tier: dict[str, int] = {}
        for entry in self._audit:
            tier = entry.get("tier", "")
            by_tier[tier] = by_tier.get(tier, 0) + 1
        return {"total_routes": len(self._audit), "by_tier": by_tier}
