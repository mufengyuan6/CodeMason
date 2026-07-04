"""Skill 生态对接测试（v1.27：本地盘点索引 + 薄适配层）。

覆盖：
- SkillRegistry：scan（复用 LazySkillLoader）/ index 持久化 / search（name/tags）/ install
- Agent Skills 标准兼容（SKILL.md frontmatter：name/description 必填）
- 未命中 Skill 零开销保持（Lazy 语义不破坏）
- install 本地复制（不覆盖已有、源不存在报错）
- registry 输出可被 Web /api/skills 消费（结构化）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.skills.registry import SkillRegistry


def _make_skill(base: Path, name: str, description: str = "desc", tags: str = "") -> Path:
    """构造符合 Agent Skills 标准的 skill 目录（SKILL.md + frontmatter）。"""
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    tags_line = f"tags: {tags}\n" if tags else ""
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{tags_line}---\n\n# {name}\n\nInstructions here.\n",
        encoding="utf-8",
    )
    return d


class TestRegistryScan:
    def test_scan_finds_skills(self, tmp_path):
        _make_skill(tmp_path / "skills", "my-skill", "Do X for Y")
        _make_skill(tmp_path / "skills", "another-skill", "Do Z")
        r = SkillRegistry(tmp_path / "skills")
        assert len(r.list_all()) == 2
        names = {s["name"] for s in r.list_all()}
        assert names == {"my-skill", "another-skill"}

    def test_scan_ignores_invalid(self, tmp_path):
        """无 SKILL.md 的目录不纳入（Agent Skills 标准：SKILL.md 是必需）。"""
        d = tmp_path / "skills" / "not-a-skill"
        d.mkdir(parents=True)
        (d / "README.md").write_text("no skill here")
        r = SkillRegistry(tmp_path / "skills")
        assert r.list_all() == []

    def test_scan_missing_dir(self, tmp_path):
        r = SkillRegistry(tmp_path / "nonexistent")
        assert r.list_all() == []


class TestRegistryIndex:
    def test_index_persistence(self, tmp_path):
        """index.json 持久化：重建 registry 后索引仍可读。"""
        _make_skill(tmp_path / "skills", "my-skill", "Do X")
        r = SkillRegistry(tmp_path / "skills", index_path=tmp_path / "index.json")
        r.rebuild_index()
        assert (tmp_path / "index.json").exists()

        r2 = SkillRegistry(tmp_path / "skills", index_path=tmp_path / "index.json")
        r2.load_index()
        assert any(s["name"] == "my-skill" for s in r2.list_all())

    def test_rebuild_updates(self, tmp_path):
        """新增 skill 后 rebuild_index 反映最新。"""
        _make_skill(tmp_path / "skills", "first", "Do A")
        r = SkillRegistry(tmp_path / "skills", index_path=tmp_path / "index.json")
        r.rebuild_index()
        _make_skill(tmp_path / "skills", "second", "Do B")
        r.rebuild_index()
        assert len(r.list_all()) == 2


class TestRegistrySearch:
    def test_search_by_name(self, tmp_path):
        _make_skill(tmp_path / "skills", "code-review", "Review code quality")
        _make_skill(tmp_path / "skills", "deploy", "Deploy to prod")
        r = SkillRegistry(tmp_path / "skills")
        hits = r.search("review")
        assert len(hits) == 1
        assert hits[0]["name"] == "code-review"

    def test_search_by_tags(self, tmp_path):
        _make_skill(tmp_path / "skills", "skill-a", "Do A", tags="devops, ci")
        _make_skill(tmp_path / "skills", "skill-b", "Do B", tags="frontend")
        r = SkillRegistry(tmp_path / "skills")
        hits = r.search("devops")
        assert len(hits) == 1
        assert hits[0]["name"] == "skill-a"

    def test_search_no_match(self, tmp_path):
        _make_skill(tmp_path / "skills", "code-review", "Review code")
        r = SkillRegistry(tmp_path / "skills")
        assert r.search("nonexistent-tag-xyz") == []


class TestRegistryInstall:
    def test_install_copies_skill(self, tmp_path):
        src = _make_skill(tmp_path / "src", "my-skill", "Do X")
        dst = tmp_path / "dst"
        r = SkillRegistry(dst)
        ok = r.install(str(src))
        assert ok is True
        assert (dst / "my-skill" / "SKILL.md").exists()
        assert "Do X" in (dst / "my-skill" / "SKILL.md").read_text(encoding="utf-8")

    def test_install_source_missing(self, tmp_path):
        r = SkillRegistry(tmp_path / "dst")
        assert r.install(str(tmp_path / "nonexistent")) is False

    def test_install_overwrite_refused(self, tmp_path):
        src = _make_skill(tmp_path / "src", "my-skill", "v1")
        dst = tmp_path / "dst"
        _make_skill(dst, "my-skill", "existing")
        r = SkillRegistry(dst)
        # 不覆盖已有（防误覆盖）
        assert r.install(str(src), overwrite=False) is False
        assert "existing" in (dst / "my-skill" / "SKILL.md").read_text(encoding="utf-8")


class TestRegistryLazyCompat:
    def test_zero_overhead_preserved(self, tmp_path):
        """Lazy 语义不破坏：未命中 skill 不加载正文。"""
        _make_skill(tmp_path / "skills", "my-skill", "Do X")
        r = SkillRegistry(tmp_path / "skills")
        # registry 只消费元数据（list_all 不触发 load）
        assert len(r.list_all()) == 1
        # 元数据清单不包含正文（零开销）
        for s in r.list_all():
            assert "content" not in s

    def test_frontmatter_compat(self, tmp_path):
        """Agent Skills 标准：name/description 从 frontmatter 解析。"""
        _make_skill(tmp_path / "skills", "std-skill", "Standard skill desc", tags="std")
        r = SkillRegistry(tmp_path / "skills")
        s = r.list_all()[0]
        assert s["name"] == "std-skill"
        assert s["description"] == "Standard skill desc"
        assert "std" in s["tags"]
