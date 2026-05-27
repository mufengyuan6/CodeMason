"""Staging 审查沙盒。

- 所有 AI 变更先进入 staging diff 视图，不直接落盘（对标 Plandex 审查沙盒）
- 链路：工具执行 → 变更进 staging → 全部 Hook 验证（YAGNI/安全/权限）→ 通过才 apply 到工作区
- Hook 拦截 = staging 中移除变更，零回滚成本
- Web 驾驶舱审批中心直接展示 staging diff

实现策略（务实版）：
- 写入类工具（Write/Edit）先产出 diff 快照存 staging（不落盘工作区）
- apply 时对 staging 文件集执行 Hook 验证，全部通过后统一落盘
"""

from __future__ import annotations

import difflib
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass
class StagedChange:
    """一条 staging 变更。"""

    change_id: str
    path: str
    old_content: str
    new_content: str
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending / applied / rejected / blocked
    hook_results: list[dict] = field(default_factory=list)
    attestation: Optional[str] = None  # SHA256 完整性校验（v1.13 G11）


class StagingSandbox:
    """Staging 沙盒：变更暂存 + Hook 验证 + apply（含 attestation 完整性校验）。"""

    def __init__(self, hooks: Optional[list[Callable[[StagedChange], dict]]] = None) -> None:
        self._changes: dict[str, StagedChange] = {}
        self._hooks = hooks or []
        self._seq = 0

    @staticmethod
    def _sha256(change: StagedChange) -> str:
        """变更集 SHA256 摘要（attestation）：old+new+path 组合哈希。"""
        payload = f"{change.path}|{change.old_content}|{change.new_content}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def stage(self, path: str, old_content: str, new_content: str) -> StagedChange:
        """暂存一个变更（不落盘）。生成 attestation 摘要（审批后比对）。"""
        self._seq += 1
        change = StagedChange(change_id=f"stg-{self._seq}", path=path, old_content=old_content, new_content=new_content)
        change.attestation = self._sha256(change)
        self._changes[change.change_id] = change
        return change

    def diff(self, change: StagedChange) -> str:
        """生成变更的 unified diff（Web 审批中心展示）。"""
        return "".join(
            difflib.unified_diff(
                change.old_content.splitlines(keepends=True),
                change.new_content.splitlines(keepends=True),
                fromfile=f"a/{change.path}",
                tofile=f"b/{change.path}",
            )
        )

    def run_hooks(self, change: StagedChange) -> bool:
        """运行全部 Hook 验证。任一 Hook 返回 blocked=True 则拦截。"""
        change.hook_results = []
        passed = True
        for hook in self._hooks:
            try:
                result = hook(change) or {}
                change.hook_results.append(result)
                if result.get("blocked"):
                    passed = False
            except Exception as e:
                change.hook_results.append({"hook": getattr(hook, "__name__", "?"), "blocked": True, "reason": str(e)})
                passed = False
        change.status = "pending" if passed else "blocked"
        return passed

    def verify_attestation(self, change: StagedChange) -> bool:
        """Attestation 完整性校验（G11）：apply 前比对 SHA256——防"审批后内容被偷偷改动"。

        篡改 = 拒绝 apply 并告警（Web 审批中心确认的 staging 内容与落盘内容必须一致）。
        """
        current = self._sha256(change)
        if change.attestation is None:
            return False
        return current == change.attestation

    def apply(self, change_id: str, *, hooks_ok: bool = True) -> dict:
        """apply 到工作区（Hook 通过 + attestation 校验通过才允许）。零回滚成本：拦截 = 移除变更。"""
        change = self._changes.get(change_id)
        if change is None:
            return {"status": "error", "error": f"未知变更: {change_id}"}
        if change.status == "blocked":
            return {"status": "blocked", "reason": [r.get("reason") for r in change.hook_results]}
        if hooks_ok and not self.run_hooks(change):
            return {"status": "blocked", "reason": [r.get("reason") for r in change.hook_results]}
        # Attestation 校验：审批内容与落盘内容必须一致（防篡改）
        if not self.verify_attestation(change):
            change.status = "blocked"
            return {"status": "tampered", "reason": "Attestation 校验失败：审批后的变更内容被修改，拒绝 apply"}
        p = Path(change.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(change.new_content, encoding="utf-8")
        change.status = "applied"
        return {"status": "applied", "change_id": change_id, "path": change.path}

    def reject(self, change_id: str) -> dict:
        """拒绝变更（staging 中移除，零落盘）。"""
        change = self._changes.get(change_id)
        if change is None:
            return {"status": "error", "error": f"未知变更: {change_id}"}
        change.status = "rejected"
        return {"status": "rejected", "change_id": change_id}

    def pending_changes(self) -> list[StagedChange]:
        return [c for c in self._changes.values() if c.status == "pending"]

    def all_diffs(self) -> list[dict]:
        """全部 pending 变更的 diff（Web 审批中心批量展示）。"""
        return [{"change_id": c.change_id, "path": c.path, "diff": self.diff(c)} for c in self.pending_changes()]
