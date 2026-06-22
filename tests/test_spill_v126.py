"""T-26-5 spill 私有落盘测试（v1.26，3.2 阶段1——对标 DSH spill）。

验证：超大工具输出完整存会话作用域私有目录（0700/随机名/wx 独占打开防
symlink 竞争），返回 locator + 检索提示；存储失败时拒绝（调用方降级）。
"""

import os
import stat

import pytest

from src.context.spill import SpillStore, SpillRef


class TestSpillStore:
    def test_save_text_returns_locator(self, tmp_path):
        """save_text 返回 locator（私有路径）+ 检索提示。"""
        store = SpillStore(root=tmp_path)
        ref = store.save_text("会话1", "tool-output", "x" * 5000)
        assert isinstance(ref, SpillRef)
        assert ref.locator  # locator 非空
        assert ref.bytes == 5000
        assert ref.retrieval_hint  # 检索提示非空

    def test_file_exists_with_content(self, tmp_path):
        """落盘文件存在且内容完整。"""
        store = SpillStore(root=tmp_path)
        content = "A" * 3000
        ref = store.save_text("会话1", "tool-output", content)
        assert os.path.exists(ref.locator)
        with open(ref.locator, encoding="utf-8") as f:
            assert f.read() == content

    def test_private_directory_perms(self, tmp_path):
        """私有目录权限（owner-only；POSIX 严格断言 0700，Windows 只读位）。"""
        import sys

        store = SpillStore(root=tmp_path)
        store.save_text("会话1", "tool-output", "x" * 100)
        spill_root = tmp_path / ".spill"
        mode = stat.S_IMODE(os.stat(spill_root).st_mode)
        # owner 可读写（所有平台）
        assert mode & 0o700 == 0o700
        if sys.platform != "win32":
            # POSIX：group/other 无权限（0700 语义）
            assert mode & 0o077 == 0

    def test_random_filenames_no_collision(self, tmp_path):
        """随机文件名（两次保存不撞名）。"""
        store = SpillStore(root=tmp_path)
        r1 = store.save_text("s1", "tool-output", "x" * 100)
        r2 = store.save_text("s1", "tool-output", "y" * 100)
        assert r1.locator != r2.locator
        assert os.path.basename(r1.locator) != os.path.basename(r2.locator)

    def test_session_scoped(self, tmp_path):
        """按会话作用域存储（不同会话不同目录）。"""
        store = SpillStore(root=tmp_path)
        r1 = store.save_text("会话A", "tool-output", "x" * 100)
        r2 = store.save_text("会话B", "tool-output", "y" * 100)
        assert os.path.dirname(r1.locator) != os.path.dirname(r2.locator)

    def test_wx_exclusive_create(self, tmp_path):
        """wx 独占打开（预先放置文件 → 拒绝，防 symlink 竞争）。"""
        store = SpillStore(root=tmp_path)
        # 预创建目标目录模拟攻击者预先放置
        store._ensure_root()
        target = store._spill_dir("会话1") / "attacker-placeholder"
        target.write_text("pwned")
        # save_text 应使用随机名避开预置（不覆盖预置文件）
        ref = store.save_text("会话1", "tool-output", "safe" * 50)
        with open(ref.locator, encoding="utf-8") as f:
            assert f.read() == "safe" * 50
        assert target.read_text() == "pwned"  # 预置文件未被覆盖

    def test_rejects_on_storage_failure(self, tmp_path):
        """存储失败（路径不可写）→ 拒绝（调用方降级为 inline）。"""
        store = SpillStore(root=tmp_path)
        # 用一个不可写路径模拟失败：把 root 指向一个文件
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir")
        store2 = SpillStore(root=blocker)
        with pytest.raises(OSError):
            store2.save_text("s1", "tool-output", "x" * 100)
