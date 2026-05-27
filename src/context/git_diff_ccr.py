"""git diff/log CCR 化（3.2 阶段2，P0）——git 操作是 token 黑洞 Top1。

- 完整 diff 存 Git Checkpoint（已有），组装时模型只见**变更统计摘要**：
  文件列表 ±行数 / 函数级变更点 / 冲突标记
- 需要细节时按 ref/事件 ID 取回完整 diff——复用已有 Checkpoint 机制零新增子系统
- 对标 RTK：git 命令输出省 60-90%
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class FileChangeSummary:
    """单文件变更统计。"""

    path: str
    added: int = 0
    removed: int = 0
    functions: list[str] = field(default_factory=list)  # 函数级变更点
    conflict: bool = False


@dataclass
class DiffSummary:
    """整体变更统计摘要（模型可见，非全量 diff）。"""

    files: list[FileChangeSummary]
    total_added: int = 0
    total_removed: int = 0
    raw_lines: int = 0

    def to_markdown(self, max_files: int = 20) -> str:
        """转 markdown 摘要（喂给模型的部分）。"""
        lines = [f"**Git Changes** ({len(self.files)} files, +{self.total_added}/-{self.total_removed})"]
        for f in self.files[:max_files]:
            entry = f"- `{f.path}` +{f.added}/-{f.removed}"
            if f.conflict:
                entry += " ⚠️ CONFLICT"
            if f.functions:
                entry += " — " + ", ".join(f.functions[:5])
            lines.append(entry)
        if len(self.files) > max_files:
            lines.append(f"- ... 另有 {len(self.files) - max_files} 个文件")
        return "\n".join(lines)


class GitDiffCcr:
    """git diff CCR：统计摘要 + 按 ref 取回完整 diff。"""

    def __init__(self, repo_path: str | Path) -> None:
        self.repo = Path(repo_path)

    def _run(self, cmd: list[str]) -> str:
        try:
            r = subprocess.run(cmd, cwd=str(self.repo), capture_output=True, text=True, timeout=60)
            return r.stdout
        except Exception:
            return ""

    def summarize(self, ref: Optional[str] = None, *, base: str = "HEAD") -> DiffSummary:
        """生成变更统计摘要（模型只见此摘要，完整 diff 按需取回）。

        - ref 省略：工作区未提交变更（git diff HEAD）
        - ref 指定：某 checkpoint/commit 的变更（git diff base..ref）
        """
        if ref:
            diff = self._run(["git", "diff", base, ref])
        else:
            diff = self._run(["git", "diff", base])
        staged = self._run(["git", "diff", "--cached"])
        diff += staged
        return self._parse_diff(diff)

    def full_diff(self, ref: Optional[str] = None, *, base: str = "HEAD") -> str:
        """按 ref 取回完整 diff（agent 需要细节时，对标 CCR retrieve）。"""
        if ref:
            return self._run(["git", "diff", base, ref])
        return self._run(["git", "diff", base]) + self._run(["git", "diff", "--cached"])

    def _parse_diff(self, diff: str) -> DiffSummary:
        files: list[FileChangeSummary] = []
        current: Optional[FileChangeSummary] = None
        total_added = total_removed = 0
        for line in diff.splitlines():
            if line.startswith("diff --git"):
                if current:
                    files.append(current)
                m = re.search(r" b/(.+)$", line)
                current = FileChangeSummary(path=m.group(1) if m else "?")
                continue
            if current is None:
                continue
            if line.startswith("+++") or line.startswith("---") or line.startswith("index "):
                continue
            if line.startswith("@@"):
                # hunk 头：@@ -a,b +c,d @@
                m = re.search(r"\+(\d+)", line)
                if m:
                    pass  # hunk 位置信号（暂不展示行号，函数级变更点更有用）
                continue
            if line.startswith("+"):
                current.added += 1
                total_added += 1
                # 函数级变更点：+ 开头且含 def/class/函数签名
                fn = re.search(r"^\+\s*(def|class|async def)\s+([a-zA-Z_]\w*)", line)
                if fn:
                    current.functions.append(fn.group(2))
            elif line.startswith("-"):
                current.removed += 1
                total_removed += 1
            if "<<<<<<<" in line:
                current.conflict = True
        if current:
            files.append(current)
        return DiffSummary(
            files=files, total_added=total_added, total_removed=total_removed,
            raw_lines=len(diff.splitlines()),
        )

    def compress_ratio(self, ref: Optional[str] = None) -> float:
        """压缩比：摘要 token / 完整 diff token（RTK 对标指标）。"""
        summary = self.summarize(ref)
        full = self.full_diff(ref)
        if not full:
            return 1.0
        return len(summary.to_markdown()) / max(len(full), 1)
