"""AGENTS.md 渐进式披露模板（G13 v1.22 落地：上下文工程实现细节）。

设计（design.md G13，对标 OpenAI AGENTS.md 渐进式披露）：
- AGENTS.md 精简为 ~100 行"目录"角色指向 docs/（ARCHITECTURE/DESIGN/PLANS/
  PRODUCT_SENSE/QUALITY_SCORE/RELIABILITY/SECURITY）
- 子目录 AGENTS.override.md 就近覆盖
- 大小上限 32KiB
- 机械架构围栏（确定性 linter 输出格式为 AI 设计 + LLM 审计 agent 双轨）+ 熵管理
  （专用清理 agent 扫文档漂移/模式违规/依赖问题——"harness 本身也会腐化"）
- CodeMason 3.2 渲染管线的"提供商体系+分层注入"的文档侧镜像

范式声明：业务逻辑层 OOP（模板生成 + 校验）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# 渐进式披露目录角色（docs/ 分层）
DOCS_ROLES: list[tuple[str, str]] = [
    ("ARCHITECTURE", "系统架构与技术决策（对应 design.md）"),
    ("DESIGN", "模块设计细节与接口契约（对应 design.md 各 G 系列）"),
    ("PLANS", "进行中的实现计划与 Search Plans"),
    ("PRODUCT_SENSE", "产品定位与叙事（对应 prd.md）"),
    ("QUALITY_SCORE", "质量评分与验收标准"),
    ("RELIABILITY", "运行韧性机制（重试/恢复/熔断）"),
    ("SECURITY", "安全模型（分类器/沙箱/凭据通道）"),
]

MAX_AGENTS_MD_BYTES = 32 * 1024  # 32KiB 上限


@dataclass
class AgentsMdTemplate:
    """AGENTS.md 模板（渐进式披露：目录角色 + docs/ 分层 + override 说明）。"""

    project_name: str
    docs_dir: str = "docs"
    extra_sections: list[str] = field(default_factory=list)
    max_bytes: int = MAX_AGENTS_MD_BYTES

    def render(self) -> str:
        """渲染 AGENTS.md 内容（~100 行目录角色）。"""
        lines = [
            f"# {self.project_name} — Agent 开发指南",
            "",
            "> 本文件是目录（渐进式披露）：不展开细节，指向 docs/ 分层文档。",
            "> 修改规则：禁止在本文件堆砌实现细节；新知识进对应 docs/ 文档，这里只加一行引用。",
            "",
            "## 项目结构",
            "",
            f"- **docs/**: 设计文档（唯一维护源）——PRD 与 Design 同号对齐演进",
            f"- **AGENTS.md**: 本文件（目录，≤100 行，≤32KiB）",
            "- **AGENTS.override.md**: 子目录就近覆盖（就近优先）",
            "",
            "## 文档角色（渐进式披露）",
            "",
            "| 角色 | 内容 | 位置 |",
            "|------|------|------|",
        ]
        for role, desc in DOCS_ROLES:
            lines.append(f"| **{role}** | {desc} | `{self.docs_dir}/` |")
        lines.append("")
        lines.append("## 开发铁律")
        lines.append("")
        lines.append("1. **一切皆事件**：状态是事件的可验证投影，快照可校验回滚，审计可回看。")
        lines.append("2. **写得更少**：YAGNI 独立验证 Hook，禁止冗余实现。")
        lines.append("3. **验证更硬**：声称即验证——变更级验证门 + 机读门禁。")
        lines.append("4. **安全是架构属性**：G18 分类器 + G19 沙箱 + 凭据独立通道。")
        lines.append("")
        for section in self.extra_sections:
            lines.append(section)
            lines.append("")
        return "\n".join(lines)


class AgentsMdValidator:
    """AGENTS.md 校验器（渐进式披露健康检查：大小上限 + 目录角色 + override 语义）。"""

    def __init__(self, max_bytes: int = MAX_AGENTS_MD_BYTES) -> None:
        self.max_bytes = max_bytes

    def validate(self, content: str) -> dict:
        """校验 AGENTS.md。

        返回 {ok, size_bytes, within_limit, has_roles, has_override_note, issues}
        """
        size = len(content.encode("utf-8"))
        issues = []
        if size > self.max_bytes:
            issues.append(f"超过 32KiB 上限（{size} bytes）——渐进式披露被破坏")
        has_roles = all(role in content for role, _ in DOCS_ROLES)
        if not has_roles:
            issues.append("缺少文档角色目录（ARCHITECTURE/DESIGN/PLANS/SECURITY 等）")
        has_override = "AGENTS.override.md" in content
        if not has_override:
            issues.append("缺少 AGENTS.override.md 就近覆盖说明")
        has_docs_link = "docs/" in content
        if not has_docs_link:
            issues.append("缺少 docs/ 分层引用")
        return {
            "ok": not issues,
            "size_bytes": size,
            "within_limit": size <= self.max_bytes,
            "has_roles": has_roles,
            "has_override_note": has_override,
            "has_docs_link": has_docs_link,
            "issues": issues,
        }


class AgentsMdManager:
    """AGENTS.md 管理器：生成 + 写入 + 校验（企业部署形态）。"""

    def __init__(self, project_root: str = ".") -> None:
        self.root = Path(project_root)
        self.validator = AgentsMdValidator()

    def generate(self, project_name: str, *, overwrite: bool = False) -> Path:
        """生成 AGENTS.md（不存在或 overwrite 时写入）。"""
        target = self.root / "AGENTS.md"
        if target.exists() and not overwrite:
            return target  # 已存在不覆盖（尊重既有）
        template = AgentsMdTemplate(project_name=project_name, docs_dir="docs")
        target.write_text(template.render(), encoding="utf-8")
        return target

    def validate_file(self) -> dict:
        """校验项目现有 AGENTS.md（不存在 → 报告缺失）。"""
        target = self.root / "AGENTS.md"
        if not target.exists():
            return {"ok": False, "issues": ["AGENTS.md 不存在（项目未初始化渐进式披露）"], "size_bytes": 0, "within_limit": True, "has_roles": False, "has_override_note": False, "has_docs_link": False}
        return self.validator.validate(target.read_text(encoding="utf-8"))
