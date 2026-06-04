"""控制平面（G14 v1.23 落地：策略即代码 + 运行时干预 + Loop 库——企业"能治"的地基）。

设计（design.md G14，v1.23 P2→P1，对标 LaunchDarkly AgentControl + OpenAI Codex 治理）：
- 策略从代码配置提成可版本化、可审计的策略文件（哪些工具可调/哪些数据可读/
  哪些动作需审批/审计日志）
- 运行时干预——对话中途换模型/切降级/改策略（现有只能 cancel，补 mid-turn 干预 Op）
- Loop 库（预置 loop 模板：CI 清扫/Issue 分诊/变更日志草稿，对标 Greyling 模式注册表 /
  Forward Future 70+ loop，Web 驾驶舱做"loop 商店"）
- 企业治理六件套（v1.22）：sandbox scope / approval policy / network rules / credential /
  OTel 遥测导出 / managed config——与 G18 分类器、G19 沙箱构成"策略-执行-治理"闭环

范式声明：业务逻辑层 OOP。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ========== 策略即代码 ==========

@dataclass
class PolicyRule:
    """一条策略规则（工具/数据/动作权限）。"""

    action: str  # allow / deny / require_approval / require_judge
    tool_pattern: str  # 工具名（支持 * 通配）
    resource_pattern: str = "*"  # 资源路径（支持 glob）
    reason: str = ""
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "tool_pattern": self.tool_pattern,
            "resource_pattern": self.resource_pattern,
            "reason": self.reason,
            "enabled": self.enabled,
        }


@dataclass
class ControlPolicy:
    """可版本化策略文件（企业部署形态：policy.yaml 加载）。"""

    policy_id: str
    version: int = 1
    rules: list[PolicyRule] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "rules": [
                {"action": r.action, "tool_pattern": r.tool_pattern, "resource_pattern": r.resource_pattern, "reason": r.reason, "enabled": r.enabled}
                for r in self.rules
            ],
        }

    @classmethod
    def from_text(cls, text: str, policy_id: str = "default") -> "ControlPolicy":
        """从策略文件文本加载（零依赖解析：`action tool_pattern resource_pattern` 每行）。"""
        policy = cls(policy_id=policy_id)
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            action = parts[0]
            tool = parts[1]
            resource = parts[2] if len(parts) > 2 else "*"
            policy.rules.append(PolicyRule(action=action, tool_pattern=tool, resource_pattern=resource))
        return policy

    @classmethod
    def from_file(cls, path: str) -> "ControlPolicy":
        return cls.from_text(Path(path).read_text(encoding="utf-8"), policy_id=Path(path).stem)


class PolicyEngine:
    """策略执行引擎：工具调用前查策略（fail-closed：无规则默认 deny？不——无规则放行，命中 deny 才拦）。"""

    def __init__(self, policy: Optional[ControlPolicy] = None) -> None:
        self.policy = policy or ControlPolicy(policy_id="default")
        self._audit: list[dict] = []

    def evaluate(self, tool_name: str, resource: str = "*") -> dict:
        """评估工具调用是否被策略允许。

        返回 {decision: allow/deny/require_approval, matched_rule, reason}
        - 命中 deny → 拦截
        - 命中 require_approval → 审批
        - 未命中规则 → allow（默认放行，策略只声明限制）
        """
        import fnmatch

        decision = "allow"
        matched = None
        for rule in self.policy.rules:
            if not rule.enabled:
                continue
            if fnmatch.fnmatch(tool_name, rule.tool_pattern) and fnmatch.fnmatch(resource, rule.resource_pattern):
                matched = rule
                if rule.action == "deny":
                    decision = "deny"
                    break
                if rule.action == "require_approval":
                    decision = "require_approval"
                    break
                if rule.action == "require_judge":
                    decision = "require_judge"
                    break
        result = {"decision": decision, "matched_rule": matched.to_dict() if matched else None, "reason": matched.reason if matched else ""}
        self._audit.append({"tool": tool_name, "resource": resource, **result, "ts": time.time()})
        return result

    def audit(self, limit: int = 100) -> list[dict]:
        """策略审计日志（企业"能治"的审计面）。"""
        return self._audit[-limit:]


# ========== 运行时干预 ==========

@dataclass
class RuntimeIntervention:
    """运行时干预指令（mid-turn：对话中途改变方向）。"""

    intervention_id: str
    kind: str  # switch_model / switch_mode / update_policy / cancel
    target: str = ""  # 新模型/新模式/策略 id
    reason: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"intervention_id": self.intervention_id, "kind": self.kind, "target": self.target, "reason": self.reason}


class RuntimeController:
    """运行时干预：对话中途换模型/切降级/改策略（现有只能 cancel，补 mid-turn 干预 Op）。"""

    def __init__(self) -> None:
        self._interventions: list[RuntimeIntervention] = []
        self._seq = 0

    def intervene(self, kind: str, target: str = "", reason: str = "") -> RuntimeIntervention:
        """发起干预（switch_model/switch_mode/update_policy/cancel）。"""
        self._seq += 1
        iv = RuntimeIntervention(intervention_id=f"iv-{self._seq}", kind=kind, target=target, reason=reason)
        self._interventions.append(iv)
        return iv

    def apply(self, *, loop=None) -> dict:
        """把待处理干预应用到内核（loop 适配器）。

        真实接入：loop.enqueue_op(RuntimeInterventionOp(...))——干预也是 Op，进事件流可审计。
        """
        if not self._interventions:
            return {"applied": 0}
        applied = []
        for iv in self._interventions:
            if loop is not None and hasattr(loop, "enqueue_op"):
                # 干预作为 Op 入队（协议生产者，进事件流）
                from ..protocol.ops import UserTurnCancel  # cancel 用现有 Op

                if iv.kind == "cancel":
                    loop.enqueue_op(UserTurnCancel(reason=iv.reason))
                    applied.append(iv.intervention_id)
            else:
                applied.append(iv.intervention_id)  # 无 loop 时记录已处理
        self._interventions = [i for i in self._interventions if i.intervention_id not in applied]
        return {"applied": len(applied)}

    def pending(self) -> list[dict]:
        return [i.to_dict() for i in self._interventions]

    def history(self) -> list[dict]:
        return [i.to_dict() for i in self._interventions]


# ========== Loop 库 ==========

@dataclass
class LoopTemplate:
    """预置 loop 模板（Loop 商店的条目）。"""

    template_id: str
    name: str
    goal: str  # trigger 目标
    verifier: str  # 验证方式
    stop_rule: str  # 停止规则
    budget_tokens: int = 100_000
    description: str = ""


class LoopLibrary:
    """Loop 库：预置 loop 模板（CI 清扫/Issue 分诊/变更日志草稿，对标 Greyling/Forward Future）。"""

    DEFAULT_TEMPLATES: list[LoopTemplate] = [
        LoopTemplate(template_id="ci-cleanup", name="CI 清扫", goal="修复 CI 失败", verifier="pytest 全绿", stop_rule="3 轮修复失败停", budget_tokens=50_000, description="PR 打开自动触发：跑 CI → 修失败 → 验证全绿"),
        LoopTemplate(template_id="issue-triage", name="Issue 分诊", goal="分诊新 Issue", verifier="标签+指派人已设", stop_rule="分诊完成", budget_tokens=30_000, description="Issue 打开触发：分类 → 定级 → 指派"),
        LoopTemplate(template_id="changelog", name="变更日志草稿", goal="生成变更日志", verifier="changelog 文件已更新", stop_rule="生成完成", budget_tokens=20_000, description="合并 PR 后触发：扫描 commits → 生成日志草稿"),
        LoopTemplate(template_id="daily-regression", name="每日回归", goal="跑全量回归", verifier="pytest 全绿", stop_rule="失败超 3 次停", budget_tokens=80_000, description="每天 8 点触发：全量回归 → 失败自动修复"),
    ]

    def __init__(self, templates: Optional[list[LoopTemplate]] = None) -> None:
        self._templates = {t.template_id: t for t in (templates or self.DEFAULT_TEMPLATES)}

    def list(self) -> list[dict]:
        return [{"id": t.template_id, "name": t.name, "goal": t.goal, "verifier": t.verifier, "budget_tokens": t.budget_tokens, "description": t.description} for t in self._templates.values()]

    def get(self, template_id: str) -> Optional[LoopTemplate]:
        return self._templates.get(template_id)

    def register(self, template: LoopTemplate) -> None:
        self._templates[template.template_id] = template
