"""团队权限矩阵（G14 v1.23 落地）——team/department/org 三级 + 可共享 vs 机密。

背景（v1.23）：
记忆团队化升级 project_scope → 权限矩阵（team/department/org 三级 + 分类：
可共享经验 vs 部门机密）——多 agent 团队协作时，什么经验/数据可跨 agent 共享、
什么必须隔离（防团队冲突/外包泄漏/离职传承问题）。

范式声明：业务逻辑层 OOP。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# 数据敏感度分类
SENSITIVITY_PUBLIC = "public"          # 可共享经验（跨 agent 全共享）
SENSITIVITY_TEAM = "team"              # 团队级（同团队 agent 共享）
SENSITIVITY_DEPARTMENT = "department"  # 部门级（部门内共享）
SENSITIVITY_SECRET = "secret"          # 部门机密（仅授权 agent）


@dataclass
class AccessRule:
    """一条权限规则：资源 → 可访问的团队层级。"""

    resource_pattern: str  # 资源路径/名称模式（glob 风格）
    min_scope: str  # 最低需要的层级（org < department < team < agent）
    sensitivity: str = SENSITIVITY_TEAM
    description: str = ""
    allow_agents: list[str] = field(default_factory=list)  # 白名单（secret 级：仅授权 agent）


class PermissionMatrix:
    """团队权限矩阵：数据/资源按敏感度分级，跨 agent 访问受控。

    层级：org > department > team > agent（org 最宽，agent 最窄）。
    访问判定：请求方层级 >= 资源 min_scope → 允许；secret 级额外要求 agent 白名单。
    public = 全共享（最低门槛 agent，任何 agent 可访问）。
    """

    SCOPE_RANK = {"org": 4, "department": 3, "team": 2, "agent": 1}
    SENSITIVITY_DEFAULT = {
        SENSITIVITY_PUBLIC: "agent",       # 全共享：最低门槛，任何 agent 可访问
        SENSITIVITY_TEAM: "team",          # 团队级：team 及以上
        SENSITIVITY_DEPARTMENT: "department",  # 部门级：department 及以上
        SENSITIVITY_SECRET: "agent",       # 机密：仅白名单 agent（层级门槛 + 白名单）
    }

    def __init__(self) -> None:
        self._rules: list[AccessRule] = []
        self._agent_scopes: dict[str, str] = {}  # agent_id → 访问层级

    def add_rule(self, resource_pattern: str, sensitivity: str = SENSITIVITY_TEAM, *, min_scope: Optional[str] = None, description: str = "", allow_agents: Optional[list[str]] = None) -> AccessRule:
        """添加资源访问规则。min_scope 未指定按敏感度默认。"""
        rule = AccessRule(
            resource_pattern=resource_pattern,
            min_scope=min_scope or self.SENSITIVITY_DEFAULT.get(sensitivity, "team"),
            sensitivity=sensitivity,
            description=description,
            allow_agents=allow_agents or [],
        )
        self._rules.append(rule)
        return rule

    def set_agent_scope(self, agent_id: str, scope: str) -> None:
        """设置 agent 的访问层级（org 最宽 / agent 最窄）。"""
        self._agent_scopes[agent_id] = scope

    def can_access(self, agent_id: str, resource: str) -> tuple[bool, str]:
        """agent 能否访问资源。返回 (允许, 理由)。"""
        agent_scope = self._agent_scopes.get(agent_id, "agent")  # 默认最窄
        if agent_scope not in self.SCOPE_RANK:
            return False, f"未知 agent 层级: {agent_scope}"
        for rule in self._rules:
            if self._match(rule.resource_pattern, resource):
                # secret 级：白名单优先（层级门槛也需满足）
                if rule.sensitivity == SENSITIVITY_SECRET:
                    if agent_id in rule.allow_agents:
                        return True, f"允许: {resource}（白名单授权 agent）"
                    return False, f"拒绝: {resource} 为部门机密，agent {agent_id} 未授权"
                # 普通敏感度：层级门槛判定
                if self.SCOPE_RANK[agent_scope] >= self.SCOPE_RANK[rule.min_scope]:
                    return True, f"允许: {resource} ({rule.sensitivity}, 需 {rule.min_scope}, 你 {agent_scope})"
                return False, f"拒绝: {resource} 需 {rule.min_scope} 级（你只有 {agent_scope}）"
        # 未匹配规则：默认按资源敏感度判断（无规则 = 公开？保守拒绝）
        return False, f"拒绝: {resource} 无匹配权限规则（默认拒绝）"

    def classify(self, sensitivity: str) -> str:
        """敏感度 → 最小访问层级（驾驶舱展示/记忆团队化用）。"""
        return self.SENSITIVITY_DEFAULT.get(sensitivity, "team")

    @staticmethod
    def _match(pattern: str, resource: str) -> bool:
        """glob 风格模式匹配（* 通配）。"""
        import fnmatch

        return fnmatch.fnmatch(resource, pattern)

    def rules(self) -> list[dict]:
        return [
            {"pattern": r.resource_pattern, "min_scope": r.min_scope, "sensitivity": r.sensitivity, "description": r.description}
            for r in self._rules
        ]
