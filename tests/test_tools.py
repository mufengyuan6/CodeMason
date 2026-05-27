"""Phase 2 测试：工具注册表 + 10 内置工具。"""

import pytest

from src.tools import ToolContext, ToolRegistry, set_registry
from src.tools.builtins import exec_tools, file_tools  # noqa: F401 触发注册


@pytest.fixture(scope="module")
def registry() -> ToolRegistry:
    """共享注册表（模块级注册只执行一次）。"""
    r = ToolRegistry()
    set_registry(r)
    # 重新导入确保注册到当前实例
    import importlib

    for mod in ("src.tools.builtins.exec_tools", "src.tools.builtins.file_tools"):
        importlib.reload(importlib.import_module(mod))
    return r


class TestToolRegistry:
    def test_ten_tools_registered(self, registry):
        """10 工具全通（验收标准）。"""
        names = {t["name"] for t in registry.list_tools()}
        assert {"Read", "Write", "Edit", "Glob", "Grep", "Bash", "Monitor", "WebSearch", "WebFetch", "AskUser"} <= names

    def test_unknown_tool_returns_error(self, registry):
        result = registry.call("NotExist", {})
        assert result["status"] == "error"

    def test_read_tool(self, tmp_path, registry):
        f = tmp_path / "a.py"
        f.write_text("line1\nline2\n", encoding="utf-8")
        result = registry.call("Read", {"path": str(f)}, ToolContext(cwd=str(tmp_path)))
        assert result["status"] == "ok"
        assert result["content"] == "line1\nline2"
        assert result["total_lines"] == 2

    def test_read_missing_file(self, tmp_path, registry):
        result = registry.call("Read", {"path": "nope.py"}, ToolContext(cwd=str(tmp_path)))
        assert result["status"] == "error"

    def test_write_tool(self, tmp_path, registry):
        result = registry.call("Write", {"path": "new.py", "content": "x = 1"}, ToolContext(cwd=str(tmp_path)))
        assert result["status"] == "ok"
        assert (tmp_path / "new.py").read_text(encoding="utf-8") == "x = 1"

    def test_edit_tool_diff(self, tmp_path, registry):
        f = tmp_path / "a.py"
        f.write_text("def f():\n    return 1\n", encoding="utf-8")
        result = registry.call("Edit", {"path": str(f), "old_string": "return 1", "new_string": "return 2"})
        assert result["status"] == "ok"
        assert "return 2" in result["diff"]
        assert "+    return 2" in result["diff"]
        assert (tmp_path / "a.py").read_text(encoding="utf-8").endswith("return 2\n")

    def test_edit_not_found(self, tmp_path, registry):
        f = tmp_path / "a.py"
        f.write_text("abc", encoding="utf-8")
        result = registry.call("Edit", {"path": str(f), "old_string": "zzz", "new_string": "yyy"})
        assert result["status"] == "error"

    def test_glob_tool(self, tmp_path, registry):
        (tmp_path / "x.py").write_text("", encoding="utf-8")
        (tmp_path / "y.js").write_text("", encoding="utf-8")
        result = registry.call("Glob", {"pattern": "*.py"}, ToolContext(cwd=str(tmp_path)))
        assert result["status"] == "ok"
        assert result["count"] == 1
        assert "x.py" in result["files"][0]

    def test_grep_tool(self, tmp_path, registry):
        f = tmp_path / "a.py"
        f.write_text("def foo():\n    pass\nfoo()\n", encoding="utf-8")
        result = registry.call("Grep", {"pattern": "foo", "path": str(tmp_path)})
        assert result["count"] >= 2
        assert all("foo" in m["content"] for m in result["matches"])

    def test_bash_tool(self, tmp_path, registry):
        result = registry.call("Bash", {"command": "echo hello"}, ToolContext(cwd=str(tmp_path)))
        assert result["status"] == "ok"
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]

    def test_bash_tool_blocks_dangerous(self, tmp_path, registry):
        """P2 修复验证：工具层黑名单硬拦截（ShellGuard，确定性）。"""
        result = registry.call("Bash", {"command": "rm -rf /etc"}, ToolContext(cwd=str(tmp_path)))
        assert result["status"] == "blocked"
        assert "ShellGuard" in result["reason"]

    def test_monitor_tool_blocks_dangerous(self, tmp_path, registry):
        result = registry.call("Monitor", {"command": "sudo rm -rf /"}, ToolContext(cwd=str(tmp_path)))
        assert result["status"] == "blocked"
        assert "ShellGuard" in result["reason"]

    def test_bash_tool_safe_override(self, tmp_path, registry):
        """SAFE_OVERRIDES：清理临时目录放行（yellow 风险，不 block）。"""
        result = registry.call("Bash", {"command": "rm -rf /tmp/cleanup-xyz"}, ToolContext(cwd=str(tmp_path)))
        assert result["status"] != "blocked"

    def test_ask_user_tool(self, tmp_path, registry):
        result = registry.call("AskUser", {"question": "继续吗？", "options": ["y", "n"]})
        assert result["status"] == "ask_user"
