"""4.1b 代码图谱 AST 索引测试（v1.23 落地：P1 升级）。"""

import pytest

from src.knowledge_graph.ast_index import AstSymbolIndex


class TestAstSymbolIndex:
    """AST 索引构建 + 一次查询替代 N 次 grep。"""

    def _project(self, tmp_path):
        """构造小项目。"""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.py").write_text(
            "import os\n\nclass AuthService:\n    def login(self, user):\n        return True\n\ndef validate_token(token):\n    return token == 'x'\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "auth2.py").write_text("def validate_token(token):\n    return True\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("docs only", encoding="utf-8")
        return tmp_path

    def test_build_index(self, tmp_path):
        idx = AstSymbolIndex(str(self._project(tmp_path)))
        count = idx.build()
        assert count >= 5  # AuthService + login + 2×validate_token + imports

    def test_query_finds_all_locations(self, tmp_path):
        """一次查询替代 N 次 grep：validate_token 在两个文件都被找到。"""
        idx = AstSymbolIndex(str(self._project(tmp_path)))
        result = idx.query("validate_token")
        files = {m["file"] for m in result.matches}
        assert "src/auth.py" in files
        assert "src/auth2.py" in files
        assert len(result.matches) == 2

    def test_query_by_kind(self, tmp_path):
        idx = AstSymbolIndex(str(self._project(tmp_path)))
        classes = idx.query_by_kind("class")
        names = {c["name"] for c in classes}
        assert "AuthService" in names

    def test_unknown_symbol(self, tmp_path):
        idx = AstSymbolIndex(str(self._project(tmp_path)))
        result = idx.query("nonexistent_symbol")
        assert result.matches == []
        assert result.token_estimate == 0

    def test_excludes_directories(self, tmp_path):
        """排除大目录（node_modules 等）。"""
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "x.js").write_text("function bad() {}", encoding="utf-8")
        idx = AstSymbolIndex(str(tmp_path))
        idx.build()
        result = idx.query("bad")
        assert result.matches == []  # node_modules 被排除

    def test_stats(self, tmp_path):
        idx = AstSymbolIndex(str(self._project(tmp_path)))
        stats = idx.stats()
        assert stats["total_symbols"] >= 5
        assert stats["files_indexed"] >= 2
        assert stats["by_kind"]["function"] >= 2

    def test_token_estimate(self, tmp_path):
        """token 估算（CodeGraph 省 token 实证：只返回摘要）。"""
        idx = AstSymbolIndex(str(self._project(tmp_path)))
        result = idx.query("validate_token")
        assert result.token_estimate > 0
        # 摘要行数有限（不返回全文）
        assert all(len(m["snippet"].splitlines()) <= 3 for m in result.matches)
