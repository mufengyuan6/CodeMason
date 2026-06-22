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


# ========== v1.26 新增（DSH agent-instructions 启发：预算管理） ==========

# 指令文件候选（去重 + overlay 语义，对标 DSH agent-instructions）
INSTRUCTION_CANDIDATES = ["AGENTS.md", "CLAUDE.md"]
LOCAL_OVERLAY_CANDIDATES = ["AGENTS.local.md", "CLAUDE.local.md"]


@dataclass
class InstructionChainItem:
    """一条指令链条目（去重后）。"""

    path: str           # 文件路径
    content: str        # 内容
    level: int = 0      # 目录深度（0=项目根，越大越具体）
    is_overlay: bool = False


@dataclass
class BudgetRenderResult:
    """预算渲染结果（含省略通知，透明审计）。"""

    rendered: str
    total_bytes: int
    omitted: list[str]       # 被完整省略的文件路径
    truncated: list[str]     # 被截断的文件路径
    notice: str              # 可见省略通知（指名路径）


def render_instruction_chain(project_root: Path, *, local_candidates: Optional[list[str]] = None, max_depth: int = 4) -> list[InstructionChainItem]:
    """扫描并去重指令文件链（内容字节一致只渲染一次）。

    - 递归扫描项目根到 max_depth 深的目录（对标 DSH：项目根到 cwd 的
      每个目录），每目录内候选文件（AGENTS.md/CLAUDE.md）去首尾空白后
      字节完全一致 → 只保留最早候选
    - 真正不同的同级文件同时应用
    - overlay（AGENTS.local.md/CLAUDE.local.md）单独收集（渲染在基础之后）
    - level = 目录深度（0=项目根，越大越具体——渲染时深目录优先保留）
    """
    root = Path(project_root)
    candidates = local_candidates or LOCAL_OVERLAY_CANDIDATES
    items: list[InstructionChainItem] = []
    seen_contents: set[str] = set()

    def _scan_dir(d: Path, level: int) -> None:
        if level > max_depth:
            return
        for base in INSTRUCTION_CANDIDATES:
            p = d / base
            if p.exists():
                content = p.read_text(encoding="utf-8").strip()
                if content in seen_contents:
                    continue  # 去重：字节一致只渲染一次
                seen_contents.add(content)
                items.append(InstructionChainItem(path=str(p), content=content, level=level))
        for overlay in candidates:
            p = d / overlay
            if p.exists():
                items.append(InstructionChainItem(path=str(p), content=p.read_text(encoding="utf-8").strip(), level=level, is_overlay=True))
        # 递归子目录（跳过隐藏/常见排除目录）
        for child in sorted(d.iterdir()):
            if child.is_dir() and not child.name.startswith((".", "node_modules", "venv", "__pycache__", ".git")):
                _scan_dir(child, level + 1)

    _scan_dir(root, 0)
    return items


class AgentsMdBudgetManager:
    """AGENTS.md 预算管理（v1.26：maxBytes 先丢宽泛再截断具体 + 可见省略通知）。

    渲染优先级：保留最具体（深目录覆盖宽泛）；先丢弃完整的宽泛文件，
    再截断最具体文件；省略/截断的路径进可见通知（透明审计）。
    """

    def __init__(self, max_bytes: int = MAX_AGENTS_MD_BYTES, *, global_only: bool = False) -> None:
        self.max_bytes = max_bytes
        self.global_only = global_only  # True = 全局文件渲染（无 overlay 参与）

    def render(self, project_root: Path) -> BudgetRenderResult:
        root = Path(project_root)
        items = render_instruction_chain(root)
        if self.global_only:
            items = [i for i in items if not i.is_overlay]  # 全局文件无 overlay

        # 预算分配：全量渲染超限 → 先丢宽泛（level 小），再截断最具体
        total = sum(len(i.content.encode("utf-8")) for i in items)
        omitted: list[str] = []
        truncated: list[str] = []
        kept = list(items)
        if total > self.max_bytes:
            # 按 level 排序：先丢宽泛（level 0），保留具体（level 大）
            kept.sort(key=lambda i: i.level)
            kept.reverse()  # 最具体的在前（保留）
            budget_used = 0
            final_kept: list[InstructionChainItem] = []
            for item in kept:
                size = len(item.content.encode("utf-8"))
                if budget_used + size <= self.max_bytes:
                    final_kept.append(item)
                    budget_used += size
                elif not final_kept:
                    # 单个文件都超限 → 截断最具体
                    truncated.append(item.path)
                    budget = self.max_bytes - budget_used
                    clipped = item.content[: max(0, budget // 4)]
                    final_kept.append(InstructionChainItem(path=item.path, content=clipped + "\n[truncated by budget]", level=item.level))
                    budget_used = self.max_bytes
                else:
                    omitted.append(item.path)
            kept = final_kept

        # 组装渲染（overlay 排最后：本地补充覆盖共享基线）
        kept.sort(key=lambda i: (i.is_overlay, i.level))
        rendered = "\n\n".join(i.content for i in kept)
        total_bytes = len(rendered.encode("utf-8"))
        notice_parts = []
        if omitted:
            notice_parts.append(f"Workspace instruction budget: omitted {len(omitted)} file(s): {', '.join(omitted)}")
        if truncated:
            notice_parts.append(f"Workspace instruction budget: truncated: {', '.join(truncated)}")
        return BudgetRenderResult(
            rendered=rendered,
            total_bytes=total_bytes,
            omitted=omitted,
            truncated=truncated,
            notice="\n".join(notice_parts),
        )
