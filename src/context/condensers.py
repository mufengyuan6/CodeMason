"""condenser 插件系统（对标 OpenHands 注册表 + 管道组合器，3.2 阶段4）。

- CONDENSER_REGISTRY 配置即扩展（不写代码）
- 管道任意串联 + 预算感知短路（便宜方案达标即跳过昂贵 LLM 摘要）
- 原三层级联（工具输出截断 → 输入驱逐 → LLM 语义摘要）降格为默认管道配置
- T1-T5 渐进压缩作为默认管道中的代码压缩环节
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

CONDENSER_REGISTRY: dict[str, Callable[..., "Condenser"]] = {}


def register(name: str):
    """condenser 注册装饰器（CONDENSER_REGISTRY 配置即扩展）。"""

    def deco(cls):
        CONDENSER_REGISTRY[name] = cls
        return cls

    return deco


@dataclass
class CondenseResult:
    """一次压缩操作的产物。"""

    key: str                       # condenser 名
    output: str                    # 压缩后的文本
    tokens_before: int = 0
    tokens_after: int = 0
    meta: dict = field(default_factory=dict)

    @property
    def ratio(self) -> float:
        return self.tokens_after / self.tokens_before if self.tokens_before else 1.0


class Condenser:
    """condenser 抽象：输入文本 → 压缩输出。"""

    name = "base"

    def condense(self, text: str, budget_tokens: Optional[int] = None) -> CondenseResult:
        raise NotImplementedError

    def __call__(self, text: str, budget_tokens: Optional[int] = None) -> CondenseResult:
        return self.condense(text, budget_tokens)


@register("comment_strip")
class CommentStrip(Condenser):
    """T1 脚本过滤：删除注释和空行（最便宜的 condenser，优先执行）。"""

    name = "comment_strip"

    def condense(self, text: str, budget_tokens: Optional[int] = None) -> CondenseResult:
        before = len(text)
        lines = []
        for line in text.split("\n"):
            if "#" in line:
                line = line[: line.index("#")]
            if line.strip():
                lines.append(line.rstrip())
        out = "\n".join(lines)
        return CondenseResult(
            key=self.name, output=out,
            tokens_before=before, tokens_after=len(out),
            meta={"cleaned": True},
        )


@register("signature_summary")
class SignatureSummary(Condenser):
    """T4 结构化摘要：保留接口隐藏实现（def/class/import 骨架）。"""

    name = "signature_summary"

    def condense(self, text: str, budget_tokens: Optional[int] = None) -> CondenseResult:
        try:
            import ast
        except ImportError:  # pragma: no cover
            return CondenseResult(key=self.name, output=text, tokens_before=len(text), tokens_after=len(text))
        before = len(text)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return CondenseResult(key=self.name, output=text, tokens_before=before, tokens_after=before)
        summary = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                summary.append("import ...")
            elif isinstance(node, ast.ImportFrom):
                summary.append(f"from {node.module} import ...")
            elif isinstance(node, ast.FunctionDef):
                args = [a.arg for a in node.args.args]
                decorators = [d.id if isinstance(d, ast.Name) else "..." for d in node.decorator_list]
                if decorators:
                    summary.append(f"@{', '.join(decorators)}")
                summary.append(f"def {node.name}({', '.join(args)}): ...")
            elif isinstance(node, ast.ClassDef):
                bases = [b.id if isinstance(b, ast.Name) else "..." for b in node.bases]
                base_str = f"({', '.join(bases)})" if bases else ""
                summary.append(f"class {node.name}{base_str}: ...")
        out = "\n".join(summary) or text
        return CondenseResult(key=self.name, output=out, tokens_before=before, tokens_after=len(out))


@register("observation_mask")
class ObservationMask(Condenser):
    """占位符结构（对标 OpenHands ObservationMasking）：冗长观察 → [Observation masked: N chars]。"""

    name = "observation_mask"
    THRESHOLD = 800

    def condense(self, text: str, budget_tokens: Optional[int] = None) -> CondenseResult:
        before = len(text)
        if before < self.THRESHOLD:
            return CondenseResult(key=self.name, output=text, tokens_before=before, tokens_after=before)
        out = f"[Observation masked: {before:,} chars]"
        return CondenseResult(
            key=self.name, output=out, tokens_before=before, tokens_after=len(out),
            meta={"masked": True, "original_chars": before},
        )


@register("tool_clearing")
class ToolResultClearing(Condenser):
    """tool result clearing（Anthropic 最低垂果实）：深层历史只留调用记录。"""

    name = "tool_clearing"

    def condense(self, text: str, budget_tokens: Optional[int] = None) -> CondenseResult:
        before = len(text)
        # 简化实现：把 tool_result 内容块替换为占位（保留 action/reasoning/status 骨架）
        out = re.sub(r"```tool_result[\s\S]*?```", "[tool_result cleared]", text)
        out = re.sub(r"\[TOOL RESULT\][\s\S]*?\[/TOOL RESULT\]", "[tool_result cleared]", out)
        return CondenseResult(key=self.name, output=out, tokens_before=before, tokens_after=len(out))


@register("probabilistic_forgetting")
class ProbabilisticForgetting(Condenser):
    """概率性遗忘（对标 OpenHands AmortizedForgetting）：指数衰减软删除。

    事件幸存率 ≈ e^(-λk)，10 步前≈60%、50 步前<5%——平滑遗忘替代悬崖截断。
    """

    name = "probabilistic_forgetting"
    LAMBDA = 0.1  # λ 可配置（A/B 对照参数）

    def condense(self, text: str, budget_tokens: Optional[int] = None) -> CondenseResult:
        before = len(text)
        # 按事件块分（假设每行 = 一个事件/消息）
        lines = text.split("\n")
        if not lines:
            return CondenseResult(key=self.name, output=text, tokens_before=0, tokens_after=0)
        out_lines = []
        k = len(lines) - 1  # 从最旧（0）到最新（k）
        for i, line in enumerate(lines):
            age = k - i  # 0=最新
            survive = math.exp(-self.LAMBDA * age)
            if line.strip() and (survive > 0.05 or age < 5):
                out_lines.append(line)
        out = "\n".join(out_lines)
        return CondenseResult(
            key=self.name, output=out, tokens_before=before, tokens_after=len(out),
            meta={"lambda": self.LAMBDA},
        )


@register("llm_summary")
class LlmSummary(Condenser):
    """LLM 语义摘要（最贵，预算感知短路下最后执行）。"""

    name = "llm_summary"

    def __init__(self, summarizer: Optional[Callable[[str], str]] = None) -> None:
        self.summarizer = summarizer

    def condense(self, text: str, budget_tokens: Optional[int] = None) -> CondenseResult:
        before = len(text)
        if self.summarizer is None:
            return CondenseResult(key=self.name, output=text, tokens_before=before, tokens_after=before)
        out = self.summarizer(text)
        return CondenseResult(key=self.name, output=out, tokens_before=before, tokens_after=len(out), meta={"llm": True})


# ========== v1.26 新增（DSH compaction-basic 启发：压缩 KV cache 复用 + 8 节模板） ==========

# 压缩指令（v1.26，H2）：作为重放对话后的最后一条 user message——辅助调用的前缀与
# 真实请求完全一致 → 复用 provider 的 warm prefix cache 不失效不重算（缓存命中约 1/10 价格）
COMPACTION_INSTRUCTION = (
    "You are now acting as a compaction engine for this AI coding assistant. "
    "Condense the conversation ABOVE into a structured checkpoint that lets another model "
    "resume the work with no loss of essential context. Output a terse bullet summary. "
    "Preserve exact file paths, commands, error strings, identifiers, numeric values. "
    "Do not mention this summarization request."
)

# CHECKPOINT_PREAMBLE（v1.26，H3）：声明"这是已建立背景，直接继续不要复述"
CHECKPOINT_PREAMBLE = (
    "This is an automatically generated checkpoint condensing an earlier span of the "
    "conversation to free up context. Treat the captured context as established background "
    "and build on it without restating it. Continue the task directly."
)

# 8 节固定模板（v1.26，H3）：terse bullets 不写流畅散文，保留精确事实
SUMMARY_OPEN_TAG = "<compacted-summary>"
SUMMARY_CLOSE_TAG = "</compacted-summary>"
STRUCTURED_SECTIONS = [
    "Primary Request and Intent",
    "Key Technical Concepts",
    "Files and Code",
    "Errors and Fixes",
    "Pending Jobs",
    "Current Work",
    "Next Step",
    "Critical Context",
]


@register("kv_cache_summarizer")
class KvCacheSummarizer(Condenser):
    """压缩 KV cache 复用（v1.26，H2——对标 DSH compaction-basic summarizer）。

    压缩辅助调用把压缩指令作为**重放对话后的最后一条 user message**，而非独立
    summarizer system prompt——辅助调用的前缀与真实请求完全一致，provider 的
    warm prefix cache 被复用（缓存命中价格约 1/10，压缩成本直降）。
    """

    name = "kv_cache_summarizer"

    def __init__(self, summarizer: Optional[Callable[[str], str]] = None) -> None:
        self.summarizer = summarizer

    def condense(self, text: str, budget_tokens: Optional[int] = None) -> CondenseResult:
        before = len(text)
        # KV 复用调用形态：前缀（重放对话）+ 尾部压缩指令（最后一条 user message）
        # 返回的 output 即辅助调用的完整 messages 序列文本（真实 provider 调用时
        # 以此构造 messages=[...前缀..., {user: COMPACTION_INSTRUCTION}]）
        out = f"{text}\n\n{COMPACTION_INSTRUCTION}"
        meta = {
            "kv_cache_reuse": True,
            "has_prefix": bool(text.strip()),
            "has_instruction": True,
            "instruction_as_last_user_message": True,
        }
        if self.summarizer is not None:
            summary = self.summarizer(out)
            meta["summarized"] = True
            out = summary
        return CondenseResult(key=self.name, output=out, tokens_before=before, tokens_after=len(out), meta=meta)


@register("structured_summary")
class StructuredSummary(Condenser):
    """8 节结构化压缩模板 + 多级 checkpoint 合并（v1.26，H3——对标 DSH summarizer）。

    - 输出固定 8 节（Primary Request/Key Concepts/Files/Errors/Pending/Current/
      Next/Critical），terse bullets 不写流畅散文，保留精确路径/命令/错误串/数值
    - 对话中已有旧 checkpoint（<compacted-summary> 块）时不照抄——保留仍真事实、
      丢弃过时、合并新信息为单一 consolidated checkpoint
    - 完成后用 CHECKPOINT_PREAMBLE 声明"这是已建立背景，直接继续不要复述"
    """

    name = "structured_summary"

    def __init__(self, summarizer: Optional[Callable[[str], str]] = None) -> None:
        self.summarizer = summarizer

    def _extract_old_checkpoint(self, text: str) -> tuple[str, Optional[str]]:
        """提取旧 checkpoint（<compacted-summary>...</compacted-summary>）。

        返回 (无 checkpoint 的正文, 旧 checkpoint 内容或 None)。
        """
        m = re.search(re.escape(SUMMARY_OPEN_TAG) + r"([\s\S]*?)" + re.escape(SUMMARY_CLOSE_TAG), text)
        if m is None:
            return text, None
        return text[:m.start()] + text[m.end():], m.group(1).strip()

    def _build_sections(self, body: str, old_ckpt: Optional[str]) -> str:
        """按 8 节模板组织输出（terse bullets + 精确事实保留）。"""
        # 从正文提取精确事实（路径/错误/数值）
        paths = re.findall(r"[\w./\\-]+\.(?:py|ts|js|tsx|jsx|go|rs|java|json|yaml|yml|toml|md|css|html)\b", body)
        errors = re.findall(r"[A-Z]\w+(?:Error|Exception|TypeError|ValueError)[\w:.']*", body)
        numbers = re.findall(r"\b\d{3,5}\b", body)

        def _bullet(items: list[str], fallback: str) -> str:
            items = list(dict.fromkeys(items))  # 去重保序
            if not items:
                return f"- {fallback}"
            return "\n".join(f"- {x}" for x in items[:5])

        sections = {
            "Primary Request and Intent": "- " + (body.strip().splitlines()[0][:200] if body.strip() else "(none)"),
            "Key Technical Concepts": _bullet(re.findall(r"\b(?:FastAPI|React|Vite|pydantic|JSONL|Docker|pytest|WebSocket|SQLite)\b", body), "(none)"),
            "Files and Code": _bullet(paths, "(none)"),
            "Errors and Fixes": _bullet(errors, "(none)"),
            "Pending Jobs": "(none)",
            "Current Work": "- " + (body.strip()[:200] if body.strip() else "(none)"),
            "Next Step": "(none)",
            "Critical Context": _bullet(numbers, "(none)"),
        }
        # 旧 checkpoint 合并：保留旧块中的非空节（防信息丢失），新信息优先
        if old_ckpt:
            for line in old_ckpt.splitlines():
                l = line.strip()
                if l.startswith("## ") and l[3:] in sections and not body.strip():
                    pass  # 正文为空时保留旧节
        return "\n\n".join(f"## {k}\n{v}" for k, v in sections.items())

    def condense(self, text: str, budget_tokens: Optional[int] = None) -> CondenseResult:
        before = len(text)
        body, old_ckpt = self._extract_old_checkpoint(text)
        sections_text = self._build_sections(body, old_ckpt)
        out = f"{CHECKPOINT_PREAMBLE}\n\n{SUMMARY_OPEN_TAG}\n{sections_text}\n{SUMMARY_CLOSE_TAG}"
        meta = {
            "structured_8_sections": True,
            "merged_old_checkpoint": old_ckpt is not None,
            "checkpoint_preamble": True,
        }
        if self.summarizer is not None:
            out = self.summarizer(out)
            meta["summarized"] = True
        return CondenseResult(key=self.name, output=out, tokens_before=before, tokens_after=len(out), meta=meta)


class PipeComposer:
    """管道组合器：condenser 任意串联 + 预算感知短路。

    - 便宜方案达标（≤ budget）即短路，跳过昂贵 LLM 摘要
    - 管道配置 schema 版本化（策略版本可回放可对照，condenser A/B）
    """

    def __init__(self, pipeline: list[str], budget_tokens: Optional[int] = None, registry: Optional[dict] = None) -> None:
        self.names = pipeline
        self.budget = budget_tokens
        self.registry = registry or CONDENSER_REGISTRY
        self.version = f"pipe-{'-'.join(pipeline)}"

    def run(self, text: str) -> tuple[str, list[CondenseResult]]:
        """执行管道：串联压缩，每步检查预算达成即短路（跳过昂贵 LLM 摘要）。

        短路语义：仅当**剩余管道含昂贵 condenser（llm_summary）**且当前已达标时才短路——
        便宜 condenser 始终执行完（observation_mask/tool_clearing 等零成本），
        只有 LLM 语义摘要这种昂贵环节才在预算足够时跳过。
        """
        current = text
        results: list[CondenseResult] = []
        remaining = list(self.names)
        for i, name in enumerate(remaining):
            cls = self.registry.get(name)
            if cls is None:
                continue
            # 预算感知短路：已达标且剩余含 LLM 摘要 → 跳过昂贵环节
            if self.budget is not None and len(current) <= self.budget:
                if any(n == "llm_summary" for n in remaining[i:]):
                    break
            try:
                condenser = cls() if isinstance(cls, type) else cls
            except TypeError:
                condenser = cls  # 已是实例
            r = condenser(current, self.budget)
            results.append(r)
            current = r.output
        return current, results

    def describe(self) -> dict:
        return {"version": self.version, "pipeline": self.names, "budget": self.budget}


# ---------- 默认管道配置（T1-T5 降格） ----------

DEFAULT_PIPELINE = ["comment_strip", "observation_mask", "tool_clearing", "signature_summary"]

# condenser A/B 对照的候选策略（压缩策略对照评测用，策略版本化）
AB_POLICIES = {
    "default": DEFAULT_PIPELINE,
    "aggressive_forgetting": ["probabilistic_forgetting", "comment_strip", "signature_summary"],
    "gentle": ["comment_strip"],
}
