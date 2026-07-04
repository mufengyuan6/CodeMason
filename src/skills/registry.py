"""Skill 生态对接（v1.27：本地盘点索引 + 薄适配层）。

设计（对接 Agent Skills 开放标准，PRD/Design v1.27）：
- 2025-12 Anthropic 发布 Agent Skills 规范，2026-03 六大 agent 原生支持
  （Claude Code/Codex/Copilot/Gemini/OpenCode/Cursor）——SKILL.md + YAML
  frontmatter（name/description 必填）已是事实标准
- 分发机制对接开放标准（npm 承载 35 万+ skills，skills-npm/skillpm 已映射），
  **不自建 registry**（自建 = 重复造轮子 + 第二个孤岛）
- 本地盘点索引 = 项目内 skill 清单可视化（scan → index.json → search → install）
- 与 LazySkillLoader 的关系：registry 是"发现层"，Loader 是"加载层"——
  复用 loader 的 _scan/_parse_metadata，Loader 一行不改（未命中零开销保持）
- 安全边界：从外部源装 skill 走 lookup-before-fetch（G15）+ 签名/扫描（ClawHub 式，第二阶段起）
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .loader import LazySkillLoader


@dataclass
class SkillRegistryEntry:
    """registry 条目（Agent Skills 标准元数据 + 本地路径）。"""

    name: str
    description: str
    path: str
    tags: list[str] = field(default_factory=list)
    version: str = "0.0.0"


class SkillRegistry:
    """Skill 本地盘点索引：scan → index.json → search → install。

    - 复用 LazySkillLoader 的目录扫描 + frontmatter 解析（单一事实源）
    - index.json 持久化（重建 registry 后索引可恢复）
    - search 支持 name/description/tags 匹配
    - install 本地复制（不覆盖已有，源不存在报错）
    """

    def __init__(self, skills_dir: str | Path, index_path: Optional[str | Path] = None) -> None:
        self.skills_dir = Path(skills_dir)
        # 默认索引放 skills 目录内部（.index.json，隐藏文件）——不污染项目根
        self.index_path = Path(index_path) if index_path else self.skills_dir / ".index.json"
        self._entries: dict[str, SkillRegistryEntry] = {}
        # 复用 LazySkillLoader 的扫描/解析能力（发现层复用，加载层不动）
        self._loader = LazySkillLoader(self.skills_dir)

    # ---------- scan / index ----------

    def rebuild_index(self) -> int:
        """扫描技能目录 → 重建内存索引 + 持久化 index.json。

        每次 rebuild 重新实例化 loader（重新扫描目录）——保证新增/删除
        skill 后索引反映最新（loader 是构造时快照，不重建会漏新 skill）。
        """
        self._loader = LazySkillLoader(self.skills_dir)
        self._entries.clear()
        for meta in self._loader._skills.values():
            # 标准 frontmatter 解析（Agent Skills 规范：name/description 独立字段，
            # 不完全依赖 loader 的 description——它把整个 frontmatter 块塞进去）
            fm = self._parse_frontmatter(meta.path)
            tags = self._parse_tags(meta.path)
            self._entries[meta.name] = SkillRegistryEntry(
                name=meta.name,
                description=(fm.get("description") or meta.description).strip(),
                path=str(meta.path),
                tags=tags,
            )
        self._persist()
        return len(self._entries)

    def load_index(self) -> int:
        """从 index.json 恢复索引（不重扫目录）。"""
        if not self.index_path.exists():
            return 0
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            for item in data:
                e = SkillRegistryEntry(
                    name=item["name"],
                    description=item.get("description", ""),
                    path=item.get("path", ""),
                    tags=item.get("tags", []),
                    version=item.get("version", "0.0.0"),
                )
                self._entries[e.name] = e
        except Exception:
            return 0
        return len(self._entries)

    def _persist(self) -> None:
        try:
            data = [
                {
                    "name": e.name,
                    "description": e.description,
                    "path": e.path,
                    "tags": e.tags,
                    "version": e.version,
                }
                for e in self._entries.values()
            ]
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            self.index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass  # 索引持久化失败不阻塞运行（内存索引仍可用）

    # ---------- query ----------

    def list_all(self) -> list[dict]:
        """全量条目（Web /api/skills 消费；只含元数据，不含正文——零开销）。"""
        if not self._entries:
            self.rebuild_index()
        return [
            {"name": e.name, "description": e.description[:200], "tags": e.tags, "version": e.version}
            for e in self._entries.values()
        ]

    def search(self, query: str) -> list[dict]:
        """按 name/description/tags 搜索（小写包含匹配）。"""
        if not self._entries:
            self.rebuild_index()
        q = query.lower()
        hits = []
        for e in self._entries.values():
            haystack = " ".join([e.name, e.description, " ".join(e.tags)]).lower()
            if q in haystack:
                hits.append(
                    {"name": e.name, "description": e.description[:200], "tags": e.tags, "version": e.version}
                )
        return hits

    # ---------- install（薄适配层：本地复制） ----------

    def install(self, source: str | Path, overwrite: bool = False) -> bool:
        """从本地路径/目录安装 skill 到 registry 目录。

        - 不覆盖已有（防误覆盖；overwrite=True 显式允许）
        - 源目录需含 SKILL.md（Agent Skills 标准）
        - 第二阶段起：外部源（GitHub/npm）走 skills CLI/skillpm 薄适配 + 签名扫描
        """
        src = Path(source)
        if not src.exists() or not src.is_dir():
            return False
        if not (src / "SKILL.md").exists():
            return False
        target = self.skills_dir / src.name
        if target.exists() and not overwrite:
            return False
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, target, dirs_exist_ok=overwrite)
        # 安装后刷新索引
        self.rebuild_index()
        return True

    # ---------- 工具 ----------

    @staticmethod
    def _parse_frontmatter(skill_path: Path) -> dict:
        """解析 SKILL.md 标准 frontmatter（Agent Skills 规范：name/description 等字段）。"""
        md = skill_path / "SKILL.md"
        if not md.exists():
            return {}
        try:
            content = md.read_text(encoding="utf-8", errors="replace")[:4000]
        except Exception:
            return {}
        if not content.startswith("---"):
            return {}
        header = content.split("---", 2)[1] if content.count("---") >= 2 else ""
        result: dict = {}
        for line in header.splitlines():
            line = line.strip()
            if ":" in line and not line.startswith("#"):
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and val and key not in ("tags",):
                    result[key] = val
        return result

    @staticmethod
    def _parse_tags(skill_path: Path) -> list[str]:
        """从 SKILL.md frontmatter 解析 tags（Agent Skills 标准扩展字段）。"""
        md = skill_path / "SKILL.md"
        if not md.exists():
            return []
        try:
            content = md.read_text(encoding="utf-8", errors="replace")[:4000]
        except Exception:
            return []
        if not content.startswith("---"):
            return []
        header = content.split("---", 2)[1] if content.count("---") >= 2 else ""
        for line in header.splitlines():
            line = line.strip()
            if line.startswith("tags:"):
                raw = line[5:].strip()
                raw = raw.strip("[]").strip('"').strip("'")
                return [t.strip() for t in raw.split(",") if t.strip()]
        return []

    def stats(self) -> dict:
        return {
            "total": len(self._entries) if self._entries else len(self.list_all()),
            "indexed": len(self._entries),
            "index_path": str(self.index_path),
        }
