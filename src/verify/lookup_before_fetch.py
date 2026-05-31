"""lookup-before-fetch（G15/4.4 v1.16 落地，对标 HalluSquatting 防御）。

设计（design.md G15）：
- 包名/仓库/URL/skill 取用前强制验证存在（查官方 registry/索引/本地依赖清单）
- 2026-07 刚公开的攻击面：幻觉名被抢注 → agent fetch → 供应链攻击
  （repo 克隆幻觉率 85%、skill 安装 100%）
- "验证先于声称"从准确性诉求升级为安全边界

范式声明：业务逻辑层 OOP。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FetchVerification:
    """资源取用前验证结果。"""

    resource: str
    resource_type: str  # package / repo / url / skill
    verified: bool
    evidence: str = ""
    risk: str = "safe"  # safe / risky / blocked

    def to_dict(self) -> dict:
        return {"resource": self.resource, "resource_type": self.resource_type, "verified": self.verified, "evidence": self.evidence, "risk": self.risk}


class LookupBeforeFetch:
    """lookup-before-fetch：资源取用前验证存在（确定性本地检查 + 可插拔 registry 查询）。

    local_known：本地已知资源清单（已安装包/已克隆仓库/本地 skill 名）。
    """

    PACKAGE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,100}$")
    URL_RE = re.compile(r"^https?://[^\s]+$")
    REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")  # owner/repo

    def __init__(self, local_known: Optional[list[str]] = None, registry_lookup: Optional[callable] = None) -> None:
        """local_known：本地已知清单；registry_lookup: 外部 registry 查询（返回 bool）。"""
        self.local_known = set(local_known or [])
        self.registry_lookup = registry_lookup
        self._history: list[FetchVerification] = []

    def verify(self, resource: str, resource_type: str = "package") -> FetchVerification:
        """验证资源存在。

        判定：
        - 本地已知清单命中 → verified
        - registry_lookup 提供 → 查 registry
        - URL：协议合法 → verified（存在性由外部查，本地校验格式）
        - 否则 → 未验证（risky，blocked 由策略决定）
        """
        # 本地已知
        if resource in self.local_known:
            result = FetchVerification(resource=resource, resource_type=resource_type, verified=True, evidence="local_known", risk="safe")
            self._history.append(result)
            return result
        # registry 查询
        if self.registry_lookup is not None:
            try:
                ok = self.registry_lookup(resource, resource_type)
                result = FetchVerification(
                    resource=resource, resource_type=resource_type, verified=ok,
                    evidence=f"registry:{ok}", risk="safe" if ok else "risky",
                )
                self._history.append(result)
                return result
            except Exception as e:
                result = FetchVerification(resource=resource, resource_type=resource_type, verified=False, evidence=f"registry_error:{e}", risk="risky")
                self._history.append(result)
                return result
        # URL 格式校验
        if resource_type == "url" and self.URL_RE.match(resource):
            result = FetchVerification(resource=resource, resource_type=resource_type, verified=True, evidence="url_format_ok", risk="safe")
            self._history.append(result)
            return result
        # 格式非法
        if resource_type == "package" and not self.PACKAGE_RE.match(resource):
            result = FetchVerification(resource=resource, resource_type=resource_type, verified=False, evidence="invalid_format", risk="blocked")
            self._history.append(result)
            return result
        # 未验证（risky：调用方决定是否拦截）
        result = FetchVerification(resource=resource, resource_type=resource_type, verified=False, evidence="not_found_in_known", risk="risky")
        self._history.append(result)
        return result

    def must_fetch(self, verification: FetchVerification) -> bool:
        """能否取用（安全边界：risky/blocked 不取用，除非人工确认）。"""
        return verification.risk == "safe"

    def history(self) -> list[dict]:
        return [v.to_dict() for v in self._history]
