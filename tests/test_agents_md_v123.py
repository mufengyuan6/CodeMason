"""G13 AGENTS.md 渐进式披露模板测试（v1.22 落地）。"""

import pytest

from src.team.agents_md import AgentsMdManager, AgentsMdTemplate, AgentsMdValidator


class TestAgentsMdTemplate:
    """模板渲染（~100 行目录角色）。"""

    def test_render_roles(self):
        tpl = AgentsMdTemplate(project_name="CodeMason")
        content = tpl.render()
        # 目录角色齐全
        for role, _ in [("ARCHITECTURE", ""), ("DESIGN", ""), ("PLANS", ""), ("PRODUCT_SENSE", ""), ("SECURITY", ""), ("RELIABILITY", "")]:
            assert role in content
        # docs/ 分层引用
        assert "docs/" in content
        # 渐进式披露语义（不堆细节）
        assert "本文件是目录" in content

    def test_render_size_under_32k(self):
        tpl = AgentsMdTemplate(project_name="CodeMason")
        assert len(tpl.render().encode("utf-8")) <= 32 * 1024

    def test_extra_sections(self):
        tpl = AgentsMdTemplate(project_name="X", extra_sections=["## 自定义", "内容"])
        content = tpl.render()
        assert "## 自定义" in content


class TestAgentsMdValidator:
    """校验（32KiB 上限 + 角色目录 + override 语义）。"""

    def test_valid_template(self):
        tpl = AgentsMdTemplate(project_name="CodeMason")
        result = AgentsMdValidator().validate(tpl.render())
        assert result["ok"] is True
        assert result["within_limit"] is True
        assert result["has_roles"] is True
        assert result["has_override_note"] is True

    def test_oversize_detected(self):
        """超 32KiB → 校验失败（渐进式披露被破坏）。"""
        tpl = AgentsMdTemplate(project_name="CodeMason")
        content = tpl.render() + "\n" + "# 堆细节" * 20000  # 膨胀
        result = AgentsMdValidator().validate(content)
        assert result["within_limit"] is False
        assert any("32KiB" in issue for issue in result["issues"])

    def test_missing_roles(self):
        result = AgentsMdValidator().validate("# 只有标题")
        assert result["ok"] is False
        assert any("文档角色" in issue for issue in result["issues"])

    def test_missing_override(self):
        tpl = AgentsMdTemplate(project_name="X")
        content = tpl.render().replace("AGENTS.override.md", "其他文件")
        result = AgentsMdValidator().validate(content)
        assert result["has_override_note"] is False


class TestAgentsMdManager:
    """管理器（生成 + 校验）。"""

    def test_generate(self, tmp_path):
        mgr = AgentsMdManager(str(tmp_path))
        path = mgr.generate("CodeMason")
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "# CodeMason" in content

    def test_generate_no_overwrite(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# 既有", encoding="utf-8")
        mgr = AgentsMdManager(str(tmp_path))
        path = mgr.generate("CodeMason")
        assert path.read_text(encoding="utf-8") == "# 既有"  # 不覆盖

    def test_generate_overwrite(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# 既有", encoding="utf-8")
        mgr = AgentsMdManager(str(tmp_path))
        path = mgr.generate("CodeMason", overwrite=True)
        assert "# CodeMason" in path.read_text(encoding="utf-8")

    def test_validate_missing(self, tmp_path):
        mgr = AgentsMdManager(str(tmp_path))
        result = mgr.validate_file()
        assert result["ok"] is False
        assert "不存在" in result["issues"][0]

    def test_validate_generated(self, tmp_path):
        mgr = AgentsMdManager(str(tmp_path))
        mgr.generate("CodeMason")
        result = mgr.validate_file()
        assert result["ok"] is True
