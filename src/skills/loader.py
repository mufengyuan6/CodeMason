"""Lazy Skills。

阶段1 name+description → 阶段2 SKILL.md → 阶段3 references
system prompt <1000 token：只注入全部技能的一行描述，命中才加载正文。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SkillMetadata:
    """技能元数据（阶段 1：仅 name + description）。"""

    name: str
    description: str
    path: Path
    loaded_stage: int = 0  # 0=未加载 / 1=元数据 / 2=SKILL.md / 3=references


class LazySkillLoader:
    """技能渐进加载器：目录扫描元数据 + 按命中加载正文。"""

    def __init__(self, skills_dir: str | Path) -> None:
        self.skills_dir = Path(skills_dir)
        self._skills: dict[str, SkillMetadata] = {}
        self._scan()

    def _scan(self) -> None:
        """扫描技能目录（每个子目录一个技能）。"""
        if not self.skills_dir.exists():
            return
        for child in self.skills_dir.iterdir():
            if child.is_dir() and (child / "SKILL.md").exists():
                meta = self._parse_metadata(child / "SKILL.md")
                if meta:
                    self._skills[meta.name] = SkillMetadata(name=meta.name, description=meta.description, path=child)

    @staticmethod
    def _parse_metadata(skill_md: Path) -> Optional[SkillMetadata]:
        """解析 SKILL.md 头部元数据（name + description）。"""
        try:
            content = skill_md.read_text(encoding="utf-8", errors="replace")[:4000]
        except Exception:
            return None
        name = skill_md.parent.name
        description = content.split("---", 2)[1] if content.startswith("---") and content.count("---") >= 2 else content[:200]
        return SkillMetadata(name=name, description=description, path=skill_md.parent)

    def list_skills(self) -> list[dict]:
        """技能清单（阶段 1 元数据：注入 system prompt 用）。"""
        return [{"name": s.name, "description": s.description[:120]} for s in self._skills.values()]

    def inject_prompt(self) -> str:
        """生成一行式技能索引（<1000 token：未命中零开销）。"""
        if not self._skills:
            return ""
        lines = ["可用技能:"]
        for s in self._skills.values():
            lines.append(f"- {s.name}: {s.description[:80]}")
        return "\n".join(lines)[:1200]

    def load(self, skill_name: str, stage: int = 3) -> Optional[dict]:
        """按需加载技能正文（阶段 2 SKILL.md → 阶段 3 references）。"""
        skill = self._skills.get(skill_name)
        if skill is None:
            return None
        skill.loaded_stage = max(skill.loaded_stage, 1)
        if stage >= 2:
            md = skill.path / "SKILL.md"
            if md.exists():
                skill.loaded_stage = max(skill.loaded_stage, 2)
        if stage >= 3:
            ref_dir = skill.path / "references"
            if ref_dir.exists() and any(ref_dir.iterdir()):
                skill.loaded_stage = max(skill.loaded_stage, 3)
        # 结果在更新 loaded_stage 之后构建
        result: dict = {"name": skill.name, "description": skill.description, "stage": skill.loaded_stage}
        if stage >= 2:
            md = skill.path / "SKILL.md"
            if md.exists():
                result["content"] = md.read_text(encoding="utf-8", errors="replace")[:12000]
        if stage >= 3:
            refs = []
            ref_dir = skill.path / "references"
            if ref_dir.exists():
                for f in sorted(ref_dir.iterdir())[:5]:
                    if f.is_file():
                        refs.append(f.name)
            result["references"] = refs
        return result

    def stats(self) -> dict:
        return {
            "total": len(self._skills),
            "loaded": sum(1 for s in self._skills.values() if s.loaded_stage > 0),
            "zero_overhead": len(self._skills) - sum(1 for s in self._skills.values() if s.loaded_stage > 0),
        }
