"""Spec 验收状态（G17④ v1.23 落地：投影层——规格资产化）。

设计（design.md G17④，衔接 G13 Search Plans）：
- Search Plans 三支柱计划文件升级为 spec = markdown 叙事 + YAML 验收块
  （id/status(draft→reviewed→frozen)/acceptance:[客观断言]），批准即冻结（frozen）
- 实现完成后 acceptance 断言跑过 = 产出 verified state（与 G17① 衔接）
- "需求→验收→代码"机读契约链，spec 版本化可审计可复用
- 不做重引擎（SDD 社区共识：plan.md + 验证循环是当代正确形态）
- openspec-plus 印证：Unambiguous requirements with testable acceptance scenarios;
  done is not subjective + vertical slices

范式声明：投影层 = 纯函数 + OOP（spec 解析/验证）。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class SpecStatus(str, Enum):
    """Spec 生命周期（draft → reviewed → frozen）。"""

    DRAFT = "draft"
    REVIEWED = "reviewed"
    FROZEN = "frozen"
    VERIFIED = "verified"  # 验收断言跑过 → 产出 verified state


@dataclass
class AcceptanceItem:
    """一条验收断言（客观、可机读验证）。"""

    id: str
    assertion: str  # 客观断言（如 "pytest tests/test_auth.py -q 全绿" / "文件 src/auth.py 存在"）
    status: str = "pending"  # pending / passed / failed / skipped


@dataclass
class Spec:
    """Spec 文件（markdown 叙事 + YAML 验收块）。"""

    spec_id: str
    title: str
    narrative: str = ""  # markdown 叙事（做什么、为什么）
    status: SpecStatus = SpecStatus.DRAFT
    acceptance: list[AcceptanceItem] = field(default_factory=list)
    version: int = 1
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "spec_id": self.spec_id,
            "title": self.title,
            "status": self.status.value,
            "acceptance": [{"id": a.id, "assertion": a.assertion, "status": a.status} for a in self.acceptance],
            "version": self.version,
            "narrative_len": len(self.narrative),
        }


class SpecManager:
    """Spec 生命周期管理：创建 → 审查 → 冻结 → 验收断言验证 → verified state。"""

    ACCEPTANCE_RE = re.compile(r"^\s*-\s+(?:\[(x| )\]\s*)?(.+)$", re.MULTILINE)

    def __init__(self, specs_dir: Optional[str] = None) -> None:
        self.specs_dir = Path(specs_dir) if specs_dir else None
        self._specs: dict[str, Spec] = {}

    def create(self, spec_id: str, title: str, narrative: str = "", acceptance: Optional[list[str]] = None) -> Spec:
        """创建 spec（draft）。acceptance 为客观断言列表。"""
        spec = Spec(
            spec_id=spec_id,
            title=title,
            narrative=narrative,
            acceptance=[AcceptanceItem(id=f"acc-{i + 1}", assertion=a) for i, a in enumerate(acceptance or [])],
        )
        self._specs[spec_id] = spec
        return spec

    def review(self, spec_id: str) -> Spec:
        """审查通过 → reviewed。"""
        return self._transition(spec_id, SpecStatus.REVIEWED)

    def freeze(self, spec_id: str) -> Spec:
        """批准即冻结（reviewed → frozen）。"""
        return self._transition(spec_id, SpecStatus.FROZEN)

    def _transition(self, spec_id: str, target: SpecStatus) -> Spec:
        spec = self._specs.get(spec_id)
        if spec is None:
            raise KeyError(f"spec 不存在: {spec_id}")
        if spec.status == SpecStatus.DRAFT and target == SpecStatus.FROZEN:
            raise ValueError("frozen 必须经过 reviewed")
        spec.status = target
        return spec

    def verify_acceptance(self, spec_id: str, *, runner: Optional[callable] = None) -> Spec:
        """跑验收断言（客观验证，非主观判断）。

        runner: callable(assertion) -> bool（跑 pytest/检查文件存在等）；
        未提供时对"文件存在"类断言做本地检查，其余标记 skipped（由外部 runner 补）。
        """
        spec = self._specs.get(spec_id)
        if spec is None:
            raise KeyError(f"spec 不存在: {spec_id}")
        if spec.status != SpecStatus.FROZEN:
            raise ValueError("只有 frozen spec 才能验证验收（批准即冻结后断言产出 verified state）")
        for item in spec.acceptance:
            try:
                if runner is not None:
                    item.status = "passed" if runner(item.assertion) else "failed"
                elif self._local_check(item.assertion):
                    item.status = "passed"
                else:
                    item.status = "skipped"  # 无 runner + 非本地可查 → 跳过（不冒充通过）
            except Exception:
                item.status = "failed"
        if all(a.status == "passed" for a in spec.acceptance) and spec.acceptance:
            spec.status = SpecStatus.VERIFIED  # 全部通过 → verified state 产出
        return spec

    @staticmethod
    def _local_check(assertion: str) -> bool:
        """本地可机读断言：文件存在类（"文件 X 存在"）。"""
        m = re.search(r"文件\s+([\w./\\-]+)\s+存在", assertion)
        if m:
            return Path(m.group(1)).exists()
        return False

    def get(self, spec_id: str) -> Optional[Spec]:
        return self._specs.get(spec_id)

    def to_spec_file(self, spec_id: str) -> str:
        """导出 markdown+YAML spec 文件内容（规格资产化）。"""
        spec = self._specs[spec_id]
        lines = [f"# {spec.title}", "", spec.narrative or "(无叙事)", "", "---", "acceptance:", f"  spec_id: {spec.spec_id}", f"  status: {spec.status.value}"]
        for a in spec.acceptance:
            lines.append(f"  - id: {a.id}")
            lines.append(f"    assertion: {a.assertion}")
            lines.append(f"    status: {a.status}")
        return "\n".join(lines)
