"""T-26-7 AGENTS.md 预算管理测试（v1.26，G13——对标 DSH agent-instructions）。

验证：内容去重（AGENTS.md/CLAUDE.md 字节一致只渲染一次）+ maxBytes 先丢
宽泛再截断具体 + 可见省略通知 + AGENTS.local.md overlay（本地补充渲染在
基础之后）+ 全局文件无 overlay。
"""

from src.team.agents_md import AgentsMdBudgetManager, render_instruction_chain


class TestDedup:
    def test_identical_files_render_once(self, tmp_path):
        """内容字节一致的 AGENTS.md/CLAUDE.md 只渲染一次。"""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "AGENTS.md").write_text("规则内容", encoding="utf-8")
        (proj / "CLAUDE.md").write_text("规则内容", encoding="utf-8")  # 字节一致
        chain = render_instruction_chain(proj)
        # 去重后只有一条（不重复消耗）
        assert len(chain) == 1

    def test_different_files_both_render(self, tmp_path):
        """真正不同的同级文件同时应用。"""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "AGENTS.md").write_text("AGENTS 规则", encoding="utf-8")
        (proj / "CLAUDE.md").write_text("CLAUDE 补充规则", encoding="utf-8")  # 不同
        chain = render_instruction_chain(proj)
        assert len(chain) == 2


class TestBudget:
    def test_budget_truncates_most_specific_last(self, tmp_path):
        """maxBytes 预算：先丢完整宽泛文件，再截断最具体文件。"""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "AGENTS.md").write_text("宽泛规则" * 100, encoding="utf-8")
        sub = proj / "sub"
        sub.mkdir()
        (sub / "AGENTS.md").write_text("具体规则" * 200, encoding="utf-8")  # 最具体（深目录）
        manager = AgentsMdBudgetManager(max_bytes=200)
        result = manager.render(proj)
        # 具体文件被截断（深目录规则优先保留但受限）
        assert result.total_bytes <= 200
        assert result.omitted  # 省略通知存在（透明审计）

    def test_omission_notice_lists_paths(self, tmp_path):
        """省略通知指名路径（透明审计：知道哪些被预算牺牲）。"""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "AGENTS.md").write_text("x" * 500, encoding="utf-8")
        manager = AgentsMdBudgetManager(max_bytes=50)
        result = manager.render(proj)
        assert result.notice  # 通知非空
        assert "AGENTS.md" in result.notice  # 指名被截断/省略的路径


class TestOverlay:
    def test_local_overlay_rendered_after_base(self, tmp_path):
        """AGENTS.local.md overlay 渲染在基础文件之后（本地补充覆盖共享基线）。"""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "AGENTS.md").write_text("共享基线", encoding="utf-8")
        (proj / "AGENTS.local.md").write_text("本地补充", encoding="utf-8")
        manager = AgentsMdBudgetManager()
        result = manager.render(proj)
        # overlay 内容在输出中（且排序在后：本地补充最后）
        assert "本地补充" in result.rendered
        base_idx = result.rendered.index("共享基线")
        local_idx = result.rendered.index("本地补充")
        assert base_idx < local_idx

    def test_global_file_no_overlay(self, tmp_path):
        """全局文件（$DSH_HOME/AGENTS.md）无 overlay（企业统一基线）。"""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "AGENTS.md").write_text("全局基线", encoding="utf-8")
        (proj / "AGENTS.local.md").write_text("项目本地", encoding="utf-8")
        # 全局渲染只取 AGENTS.md（无 local overlay 参与）
        manager = AgentsMdBudgetManager(global_only=True)
        result = manager.render(proj)
        assert "项目本地" not in result.rendered
        assert "全局基线" in result.rendered
