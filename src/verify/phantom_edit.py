"""变更级验证门（G11 v1.16 落地：phantom-edit 检测）。

设计（design.md G11）：
- 每个 Write/Edit 变更走 SHA256 前后比对——"声称改了但 checksum 没变"= 拦截，
  禁止声称文件已更新
- staging apply 前置最小验证集（语法/类型/受影响测试）通过才落盘
- 验证从"任务完成判定"下沉到"每次落盘"（verification is normal not exceptional）
- PreToolUse 记 SHA256 → PostToolUse 比对

范式声明：业务逻辑层 OOP。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional


class PhantomEditDetector:
    """phantom-edit 检测：声称改了文件 → SHA256 前后比对。

    "声称改了但 checksum 没变" = phantom-edit（假变更）→ 拦截并警告。
    """

    def __init__(self) -> None:
        self._before: dict[str, str] = {}  # path → SHA256（PreToolUse 记录）

    def snapshot_before(self, path: str) -> str:
        """PreToolUse：记录文件当前 SHA256。"""
        digest = self._sha256(path)
        self._before[path] = digest
        return digest

    def verify_change(self, path: str) -> dict:
        """PostToolUse：比对前后 SHA256，判定是否真实变更。

        返回：{changed, phantom, before, after, path}
        - changed=True：内容真的变了（checksum 不同）
        - phantom=True：声称改了但 checksum 没变（拦截信号）
        - before 为空：该文件之前未快照（无法判定 → 视为未快照，不拦但提示）
        """
        after = self._sha256(path)
        before = self._before.get(path)
        if before is None:
            return {"changed": False, "phantom": False, "before": None, "after": after, "path": path, "note": "no_before_snapshot"}
        return {
            "changed": before != after,
            "phantom": before == after,  # checksum 没变 = phantom-edit
            "before": before,
            "after": after,
            "path": path,
        }

    def verify_content_change(self, claimed_old: str, actual_old: str, new_content: str) -> dict:
        """内容级 phantom-edit 检测（Write/Edit 工具层）。

        claimed_old：agent 声称的旧内容；actual_old：磁盘实际旧内容。
        若声称的旧内容与实际不符 → 变更可能基于错误前提（防"照着旧文件改"）。
        """
        claimed_digest = hashlib.sha256(claimed_old.encode("utf-8")).hexdigest()[:16]
        actual_digest = hashlib.sha256(actual_old.encode("utf-8")).hexdigest()[:16]
        new_digest = hashlib.sha256(new_content.encode("utf-8")).hexdigest()[:16]
        return {
            "phantom": claimed_digest == new_digest,  # 声称的"新内容"与"旧内容"相同 = 假变更
            "stale_base": claimed_digest != actual_digest,  # 基于过期旧内容（需 warn）
            "claimed_old_digest": claimed_digest,
            "actual_old_digest": actual_digest,
            "new_digest": new_digest,
        }

    @staticmethod
    def _sha256(path: str) -> str:
        try:
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
        except OSError:
            return ""
