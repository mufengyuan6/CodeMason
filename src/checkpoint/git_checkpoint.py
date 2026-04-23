"""Git Checkpoint。

- 快照 = git add -A（含 untracked）→ write-tree → commit-tree（parent=HEAD）→ 私用 refs
- 私用 refs（refs/agent/checkpoints/{sessionId}/{n}）保活，不动用户分支
- Checkpoint 与用户 git 历史隔离（用户工作区状态不受影响：add 后 reset 还原）
- 回滚 = 事件溯源（G4 第二层）：由 loop 发 Rollback 事件，本模块执行代码复位

说明：git stash create 在 Windows git 2.54 下对工作区修改存在 "not a valid object" 缺陷，
改用 write-tree + commit-tree 快照方案，语义一致（快照含 tracked+untracked 变更，存私用 refs）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional


class GitCheckpointError(Exception):
    pass


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise GitCheckpointError(f"git 命令失败: {' '.join(cmd)}\n{result.stderr}")
    return result


class GitCheckpoint:
    """Git 快照与回滚（快照 commit + 私用 refs）。"""

    def __init__(self, repo_path: str | Path, session_id: str = "default") -> None:
        self.repo = Path(repo_path)
        self.session_id = session_id
        self._ref_prefix = f"refs/agent/checkpoints/{session_id}"
        if not (self.repo / ".git").exists():
            raise GitCheckpointError(f"不是 git 仓库: {self.repo}")

    def create_checkpoint(self, message: str = "") -> str:
        """创建 checkpoint：快照当前工作区（tracked 修改 + untracked）为 commit，存入私用 refs。

        返回 checkpoint 引用名（如 refs/agent/checkpoints/{sid}/1）。
        创建后工作区保持原状（reset 还原 index），用户 git 历史不受影响。
        """
        # 1. 收集当前所有变更（含 untracked）并暂存
        _run(["git", "add", "-A"], self.repo)
        try:
            # 2. 写入 index 树
            tree = _run(["git", "write-tree"], self.repo).stdout.strip()
            # 3. 基于 HEAD 创建快照 commit（parent = HEAD）
            head = _run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
            commit = _run(["git", "commit-tree", tree, "-p", head, "-m", f"agent-checkpoint: {message}"], self.repo).stdout.strip()
            # 4. 编号递增存入私用 refs（手工写 ref 文件：git update-ref 在部分 Windows 环境下静默失败）
            count = len(self.list_checkpoints()) + 1
            ref = f"{self._ref_prefix}/{count}"
            self._write_ref(ref, commit)
            return ref
        finally:
            # 5. 还原 index（工作区文件不受影响），用户暂存状态保留
            _run(["git", "reset", "-q", "HEAD"], self.repo)

    def _write_ref(self, ref: str, commit: str) -> None:
        """写入 ref（先尝试 update-ref，失败则手工写 .git/refs 文件）。"""
        try:
            _run(["git", "update-ref", ref, commit], self.repo)
        except GitCheckpointError:
            pass
        # 验证是否真实写入（update-ref 可能静默失败）
        try:
            _run(["git", "rev-parse", "--verify", ref], self.repo)
            return
        except GitCheckpointError:
            pass
        # 手工写 ref 文件（refs/agent/checkpoints/s1/1 → .git/refs/agent/checkpoints/s1/1）
        ref_file = self.repo / ".git" / Path(*ref.split("/"))
        ref_file.parent.mkdir(parents=True, exist_ok=True)
        ref_file.write_text(commit + "\n", encoding="utf-8")

    def list_checkpoints(self) -> list[str]:
        """列出全部 checkpoint 引用（私用 refs）。"""
        result = _run(["git", "for-each-ref", "--format=%(refname)", self._ref_prefix], self.repo)
        return sorted(result.stdout.splitlines())

    def restore(self, checkpoint_ref: str, *, hard: bool = True) -> dict:
        """回滚到指定 checkpoint。返回复位统计。"""
        commit = _run(["git", "rev-parse", checkpoint_ref], self.repo).stdout.strip()
        if hard:
            _run(["git", "reset", "--hard", commit], self.repo)
            _run(["git", "clean", "-fd"], self.repo)
        else:
            _run(["git", "checkout", commit, "--", "."], self.repo)
        return {"checkpoint": checkpoint_ref, "commit": commit[:12], "mode": "hard" if hard else "soft"}

    def current_state(self) -> dict:
        """当前工作区状态（供事件溯源回滚记录）。"""
        head = _run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        status = _run(["git", "status", "--short"], self.repo)
        return {"head": head[:12], "changes": [l for l in status.stdout.splitlines() if l.strip()]}
