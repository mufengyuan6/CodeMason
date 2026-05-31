"""git worktree 并行隔离（G14 v1.22 落地：防 Parallel Collision）。

设计（design.md G14）：
- git worktree 每 agent 一个工作树，与用户 git 分支隔离
- 并行 Subagents（4.2）改同一仓库不同区域不互相覆盖
- 复用 2.3 Checkpoint 隔离思路（三-parent stash 的隔离语义扩展到工作树）
- Loop 七失败模式 Parallel Collision 的对策

范式声明：业务逻辑层 OOP。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Worktree:
    """一个 agent 工作树。"""

    name: str
    path: str
    branch: str
    created_at: float = 0.0


class WorktreeManager:
    """git worktree 管理器：每 agent 一个隔离工作树。

    命令：git worktree add <path> -b <branch>（与用户分支隔离）。
    """

    def __init__(self, repo_path: str) -> None:
        self.repo = Path(repo_path)
        self._worktrees: dict[str, Worktree] = {}
        self._seq = 0

    def git(self, *args: str, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
        """执行 git 命令（含 worktree 支持检查）。"""
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
            cwd=cwd or str(self.repo),
        )

    def worktrees_supported(self) -> bool:
        """探测 git worktree 支持（老版本/非 git 仓库返回 False）。"""
        r = self.git("worktree", "list")
        return r.returncode == 0

    def create(self, name: str, base_branch: str = "main") -> Optional[Worktree]:
        """为 agent 创建独立工作树（branch 隔离，防并行覆盖）。"""
        if not self.worktrees_supported():
            return None
        self._seq += 1
        branch = f"agent/{name}"
        path = str(self.repo / f".worktrees/{name}")
        r = self.git("worktree", "add", path, "-b", branch, base_branch)
        if r.returncode != 0:
            return None
        wt = Worktree(name=name, path=path, branch=branch)
        self._worktrees[name] = wt
        return wt

    def list(self) -> list[dict]:
        r = self.git("worktree", "list")
        if r.returncode != 0:
            return []
        out = []
        for line in r.stdout.strip().splitlines():
            parts = line.split()
            if parts:
                out.append({"path": parts[0], "branch": parts[1] if len(parts) > 1 else ""})
        return out

    def remove(self, name: str) -> bool:
        wt = self._worktrees.get(name)
        if wt is None:
            return False
        r = self.git("worktree", "remove", wt.path)
        if r.returncode == 0:
            del self._worktrees[name]
            return True
        return False

    def get(self, name: str) -> Optional[Worktree]:
        return self._worktrees.get(name)
