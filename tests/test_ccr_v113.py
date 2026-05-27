"""v1.13 schema 动态裁剪 + 错误结构化压缩 + git diff CCR 测试（T-F）。

验收标准（design 3.2 阶段1/2 + 强制测试要求）：
- schema 动态裁剪：最近 N 轮未用工具从发送 schema 摘除，用时恢复
- 错误结构化压缩：报错只取根因帧（过滤框架内部帧），测试输出只留 failed 行+diff
- git diff CCR：模型只见变更统计摘要，按 ref 取回完整 diff
"""

import subprocess

import pytest

from src.context import ErrorCompressor, GitDiffCcr, ToolSchemaPruner


def _sample_tools(n: int = 12) -> list[dict]:
    return [{"name": f"Tool{i}", "description": f"工具 {i}"} for i in range(n)]


class TestSchemaPruner:
    def test_prune_unused_tools(self):
        """最近 N 轮未用工具从发送 schema 摘除。"""
        pruner = ToolSchemaPruner(_sample_tools(12), recent_window=6, min_keep=2)
        # 只用 2 个工具
        pruner.observe_call("Tool0")
        pruner.observe_call("Tool1")
        pruner.observe_call("Tool0")
        sent = pruner.prune()
        names = [t["name"] for t in sent]
        assert "Tool0" in names and "Tool1" in names
        assert "Tool11" not in names  # 未使用被摘除
        assert pruner.stats.total_removed >= 1

    def test_restore_on_use(self):
        """用时恢复：工具被调用后下次组装自动包含。"""
        pruner = ToolSchemaPruner(_sample_tools(12), recent_window=6)
        pruner.observe_call("Tool0")
        pruner.prune()
        # Tool5 之前没被用过 → 摘除；现在用了 → 恢复
        assert "Tool5" not in [t["name"] for t in pruner.prune()]
        pruner.restore("Tool5")
        assert "Tool5" in [t["name"] for t in pruner.prune()]
        assert pruner.stats.restored >= 1

    def test_force_keep_never_pruned(self):
        """常驻工具（AskUser/Monitor）永不裁剪。"""
        pruner = ToolSchemaPruner(_sample_tools(12), recent_window=6)
        pruner.force_keep("Tool0", "Tool1")
        pruner.observe_call("Tool2")
        sent = pruner.prune()
        names = [t["name"] for t in sent]
        assert "Tool0" in names and "Tool1" in names

    def test_min_keep_protection(self):
        """裁剪过头保护：至少保留 min_keep 个工具。"""
        pruner = ToolSchemaPruner(_sample_tools(12), recent_window=6, min_keep=4)
        pruner.observe_call("Tool0")  # 只用 1 个
        sent = pruner.prune()
        assert len(sent) >= 4

    def test_snapshot_report(self):
        pruner = ToolSchemaPruner(_sample_tools(12), recent_window=6)
        pruner.observe_call("Tool0")
        snap = pruner.snapshot()
        assert snap["total_tools"] == 12
        assert snap["stats"]["total_removed"] >= 1


class TestErrorCompressor:
    def test_traceback_root_cause_frames(self):
        """报错只取根因帧：过滤框架内部帧，保留应用代码帧。"""
        tb = """Traceback (most recent call last):
  File "C:\\Users\\x\\venv\\Lib\\site-packages\\fastapi\\routing.py", line 291, in app
    await dependant.func(**values)
  File "C:\\Users\\x\\venv\\Lib\\site-packages\\pydantic\\main.py", line 890, in validate
    raise ValidationError
  File "D:\\project\\src\\app\\main.py", line 42, in login
    user = db.query(User).filter_by(name=name).first()
  File "D:\\project\\src\\app\\db.py", line 15, in _connect
    raise ConnectionError("db down")
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such table: users"""
        r = ErrorCompressor().compress_traceback(tb)
        # 框架内部帧被过滤
        assert not any("site-packages" in f for f in r.root_cause_frames)
        # 应用代码帧保留（根因）
        assert any("src\\app\\main.py" in f or "src/app/main.py" in f for f in r.root_cause_frames)
        assert any("db.py" in f for f in r.root_cause_frames)
        # 错误消息保留
        assert any("OperationalError" in f for f in r.related_lines)
        # 压缩有效
        assert r.compressed_lines < r.original_lines

    def test_test_output_only_failed(self):
        """测试输出只留 failed/error 行 + diff 摘要。"""
        out = """============================= test session starts =============================
collecting ... collected 5 items

test_app.py::test_login PASSED                                     [ 20%]
test_app.py::test_register FAILED                                  [ 40%]
FAILED test_app.py::test_register - AssertionError: 200 != 400
E   AssertionError: assert 200 == 400

----------------------------- Captured diff -----------------------------
     def register():
-        return 200
+        return 400
     def login():
========================= short test summary info =========================
FAILED test_app.py::test_register - AssertionError: 200 != 400"""
        r = ErrorCompressor().compress_test_output(out)
        # PASSED 行被过滤
        assert not any("PASSED" in f for f in r.failed_lines)
        # failed 行保留
        assert any("FAILED" in f for f in r.failed_lines)
        # diff 行保留
        assert any("-" in d and "return 200" in d for d in r.diff_lines)
        assert any("+" in d and "return 400" in d for d in r.diff_lines)
        assert r.compressed_lines < r.original_lines


class TestGitDiffCcr:
    def _init_repo(self, tmp_path):
        """初始化 git 仓库并做一次修改。"""
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True, capture_output=True)
        (tmp_path / "app.py").write_text("def hello():\n    return 'old'\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True, capture_output=True)
        (tmp_path / "app.py").write_text("def hello():\n    return 'new'\n\ndef extra():\n    return 1\n", encoding="utf-8")
        return tmp_path

    def test_summary_stats(self, tmp_path):
        """模型只见变更统计摘要：文件列表 ±行数 / 函数级变更点。"""
        repo = self._init_repo(tmp_path)
        ccr = GitDiffCcr(repo)
        summary = ccr.summarize()
        assert summary.total_added >= 1
        assert summary.total_removed >= 1
        # 函数级变更点检测
        assert any("extra" in f.functions for f in summary.files)
        md = summary.to_markdown()
        assert "app.py" in md
        assert "+" in md

    def test_full_diff_retrievable(self, tmp_path):
        """按需取回完整 diff（CCR retrieve）。"""
        repo = self._init_repo(tmp_path)
        ccr = GitDiffCcr(repo)
        full = ccr.full_diff()
        assert "return 'new'" in full
        assert "return 'old'" in full

    def test_compress_ratio(self, tmp_path):
        """压缩比：摘要远小于完整 diff。"""
        repo = self._init_repo(tmp_path)
        ccr = GitDiffCcr(repo)
        ratio = ccr.compress_ratio()
        assert 0 < ratio < 1
