"""Phase 2 测试：Git Checkpoint（三-parent stash + 私用 refs）+ Staging 沙盒。"""

import subprocess

import pytest

from src.checkpoint import GitCheckpoint
from src.staging import StagingSandbox


@pytest.fixture
def git_repo(tmp_path):
    """创建带初始 commit 的 git 仓库。"""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "main.py").write_text("VERSION = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True)
    return tmp_path


class TestGitCheckpoint:
    def test_create_and_list(self, git_repo):
        # 修改工作区后打点
        (git_repo / "main.py").write_text("VERSION = 2\n", encoding="utf-8")
        cp = GitCheckpoint(git_repo, session_id="s1")
        ref = cp.create_checkpoint("改版本号")
        assert ref.startswith("refs/agent/checkpoints/s1/")
        checkpoints = cp.list_checkpoints()
        assert len(checkpoints) == 1
        assert checkpoints[0] == ref

    def test_restore_rolls_back(self, git_repo):
        (git_repo / "main.py").write_text("VERSION = 2\n", encoding="utf-8")
        cp = GitCheckpoint(git_repo, session_id="s1")
        ref = cp.create_checkpoint("改版本号")
        # 再次修改（模拟改坏）
        (git_repo / "main.py").write_text("VERSION = 999\nbroken!!!", encoding="utf-8")
        result = cp.restore(ref)
        assert result["mode"] == "hard"
        content = (git_repo / "main.py").read_text(encoding="utf-8")
        assert content == "VERSION = 2\n"  # 回滚到 checkpoint

    def test_checkpoints_isolated_from_user_refs(self, git_repo):
        """checkpoint 不动用户分支（私用 refs 隔离）。"""
        cp = GitCheckpoint(git_repo, session_id="s2")
        (git_repo / "main.py").write_text("VERSION = 3\n", encoding="utf-8")
        cp.create_checkpoint("t")
        branches = subprocess.run(["git", "-C", str(git_repo), "branch"], capture_output=True, text=True, check=True)
        assert branches.stdout.strip() == "* master" or branches.stdout.strip() == "* main"
        refs = subprocess.run(["git", "-C", str(git_repo), "for-each-ref", "--format=%(refname)", "refs/heads/"], capture_output=True, text=True, check=True)
        assert "agent/checkpoints" not in refs.stdout

    def test_untracked_files_restored(self, git_repo):
        (git_repo / "main.py").write_text("VERSION = 2\n", encoding="utf-8")
        (git_repo / "new_file.py").write_text("x = 1\n", encoding="utf-8")  # untracked
        cp = GitCheckpoint(git_repo, session_id="s1")
        ref = cp.create_checkpoint("含 untracked")
        assert len(cp.list_checkpoints()) == 1
        # 快照含 untracked（write-tree 已暂存全部）
        (git_repo / "new_file.py").write_text("broken!!\n", encoding="utf-8")  # 改坏 untracked
        result = cp.restore(ref)
        assert result["mode"] == "hard"
        # 恢复后 untracked 文件内容为快照状态
        assert (git_repo / "new_file.py").read_text(encoding="utf-8") == "x = 1\n"


class TestStagingSandbox:
    def test_stage_and_diff(self, tmp_path):
        sb = StagingSandbox()
        change = sb.stage("a.py", "old line\n", "new line\n")
        diff = sb.diff(change)
        assert "-old line" in diff
        assert "+new line" in diff

    def test_apply_writes_file(self, tmp_path):
        sb = StagingSandbox()
        sb.stage(str(tmp_path / "a.py"), "old\n", "new\n")
        change = sb.pending_changes()[0]
        result = sb.apply(change.change_id)
        assert result["status"] == "applied"
        assert (tmp_path / "a.py").read_text(encoding="utf-8") == "new\n"

    def test_hook_blocks_apply(self, tmp_path):
        def evil_hook(change):
            return {"blocked": True, "reason": "YAGNI 拦截：代码行数超过阈值"}

        sb = StagingSandbox(hooks=[evil_hook])
        sb.stage(str(tmp_path / "a.py"), "old\n", "new\n" * 100)
        change = sb.pending_changes()[0]
        result = sb.apply(change.change_id)
        assert result["status"] == "blocked"
        assert not (tmp_path / "a.py").exists()  # 零落盘

    def test_hook_blocks_but_file_never_written(self, tmp_path):
        """G11：Hook 拦截 = staging 中移除，零回滚成本。"""
        def hook(change):
            return {"blocked": True, "reason": "no"}

        sb = StagingSandbox(hooks=[hook])
        sb.stage(str(tmp_path / "secret.py"), "", "rm -rf /")
        change = sb.pending_changes()[0]
        assert sb.run_hooks(change) is False
        assert change.status == "blocked"
        assert not (tmp_path / "secret.py").exists()

    def test_reject_removes(self, tmp_path):
        sb = StagingSandbox()
        sb.stage("a.py", "old\n", "new\n")
        change = sb.pending_changes()[0]
        result = sb.reject(change.change_id)
        assert result["status"] == "rejected"
        assert sb.pending_changes() == []

    def test_pass_hooks_apply(self, tmp_path):
        def ok_hook(change):
            return {"blocked": False}

        sb = StagingSandbox(hooks=[ok_hook])
        sb.stage(str(tmp_path / "a.py"), "old\n", "new\n")
        change = sb.pending_changes()[0]
        assert sb.run_hooks(change) is True
        result = sb.apply(change.change_id)
        assert result["status"] == "applied"

    def test_all_diffs(self, tmp_path):
        sb = StagingSandbox()
        sb.stage("a.py", "old\n", "new\n")
        sb.stage("b.py", "x\n", "y\n")
        diffs = sb.all_diffs()
        assert len(diffs) == 2
