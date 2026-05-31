"""事实核查子代理（G15 v1.16/v1.20 落地：声称即验证）。

设计（design.md G15）：
- 只读子代理（Read/Grep/Glob/Bash，不写代码、不改文件），挂三个节点：非平凡决策前 /
  交付声明前 / 引入新依赖时
- 对每个事实声称独立验证，输出 VERIFIED / WRONG / UNVERIFIABLE 三态 + 证据（file:line 引用）
- "不接受主会话声称当证据"——独立验证，不采信先前 assistant 话语
- mea-loop 实证：独立 Auditor 重解析文档 XML 抓到基线 agent"假装完成"（0.00 vs 0.89 分）
- 检查项：phantom-edit / placeholder/空实现 / trivial-pass 测试 / scope 收窄

范式声明：业务逻辑层 OOP。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class FactCheckResult:
    """事实核查结果。"""

    claim: str
    status: str  # VERIFIED / WRONG / UNVERIFIABLE
    evidence: list[str] = field(default_factory=list)  # file:line 引用
    checks: list[str] = field(default_factory=list)  # 执行的检查项

    def to_dict(self) -> dict:
        return {"claim": self.claim, "status": self.status, "evidence": self.evidence, "checks": self.checks}


class FactChecker:
    """事实核查器：对声称做确定性/半确定性验证（不接受主会话声称当证据）。

    纯本地实现（确定性检查）；LLM 子代理版本可注入（fresh-context 只读子代理）。
    """

    def __init__(self, project_root: str = ".") -> None:
        self.root = Path(project_root)
        self._results: list[FactCheckResult] = []

    # ---------- 三态判定 ----------

    def check_file_exists(self, claim: str, path: str) -> FactCheckResult:
        """VERIFIED/WRONG：文件是否存在。"""
        exists = (self.root / path).exists()
        status = "VERIFIED" if exists else "WRONG"
        result = FactCheckResult(claim=claim, status=status, evidence=[f"{path}:exists={exists}"], checks=["file_exists"])
        self._results.append(result)
        return result

    def check_contains(self, claim: str, path: str, needle: str) -> FactCheckResult:
        """VERIFIED/WRONG：文件内容包含某模式（验证"已实现 X"类声称）。"""
        try:
            content = (self.root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            result = FactCheckResult(claim=claim, status="WRONG", evidence=[f"{path}:unreadable"], checks=["contains"])
            self._results.append(result)
            return result
        found = needle in content
        status = "VERIFIED" if found else "WRONG"
        line_no = next((i + 1 for i, line in enumerate(content.split("\n")) if needle in line), None)
        result = FactCheckResult(
            claim=claim,
            status=status,
            evidence=[f"{path}:{line_no}" if line_no else f"{path}:not-found"],
            checks=["contains"],
        )
        self._results.append(result)
        return result

    def check_no_placeholders(self, claim: str, path: str) -> FactCheckResult:
        """placeholder/空实现检查：声称完成 → 无 TODO/FIXME/pass-only/空函数体。"""
        try:
            content = (root := self.root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            result = FactCheckResult(claim=claim, status="WRONG", evidence=[f"{path}:unreadable"], checks=["no_placeholders"])
            self._results.append(result)
            return result
        placeholders = []
        for pat in (r"\bTODO\b", r"\bFIXME\b", r"\bpass\s*$", r"NotImplemented", r"raise\s+NotImplementedError"):
            if re.search(pat, content, re.MULTILINE):
                placeholders.append(pat)
        status = "VERIFIED" if not placeholders else "WRONG"
        result = FactCheckResult(
            claim=claim,
            status=status,
            evidence=[f"{path}:placeholders={placeholders}"] if placeholders else [f"{path}:clean"],
            checks=["no_placeholders"],
        )
        self._results.append(result)
        return result

    def check_scope_words(self, claim: str, path: str) -> FactCheckResult:
        """scope 收窄检查：声称"完成"但出现 only/for now/partial 收窄词。"""
        try:
            content = (self.root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            result = FactCheckResult(claim=claim, status="WRONG", evidence=[f"{path}:unreadable"], checks=["scope_words"])
            self._results.append(result)
            return result
        narrowed = [w for w in ("only", "for now", "partial", "暂时", "先这样", "todo") if w in content.lower()]
        status = "WRONG" if narrowed else "VERIFIED"
        result = FactCheckResult(
            claim=claim,
            status=status,
            evidence=[f"{path}:narrow_words={narrowed}"] if narrowed else [f"{path}:no-narrow"],
            checks=["scope_words"],
        )
        self._results.append(result)
        return result

    # ---------- 查询 ----------

    def history(self) -> list[dict]:
        return [r.to_dict() for r in self._results]
