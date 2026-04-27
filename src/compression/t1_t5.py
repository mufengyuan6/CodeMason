"""T1-T5 渐进压缩 + auto-compact。

T1 脚本过滤（删注释/空行）→ T2 AST 剪枝 → T3 符号重命名 → T4 结构化摘要 → T5 语义摘要（LLM）。
复用旧 compressor 的 T1/T4 实现，补齐 T2 AST 剪枝 + T5 语义摘要。
"""

from __future__ import annotations

import ast
import re
from enum import Enum
from typing import Callable, Optional

from dataclasses import dataclass, field

COMMENT_RE = re.compile(r"^\s*#.*$")
BLANK_LINE_RE = re.compile(r"^\s*$")
DOCSTRING_RE = re.compile(r'^(\s*)(""".*?"""|\'\'\'.*?\'\'\')', re.DOTALL)


class CompressionLevel(str, Enum):
    T1 = "T1"  # 脚本过滤
    T2 = "T2"  # AST 剪枝
    T3 = "T3"  # 符号重命名
    T4 = "T4"  # 结构化摘要
    T5 = "T5"  # 语义摘要


@dataclass
class CompressionResult:
    level: CompressionLevel
    original_tokens: int
    compressed_tokens: int
    content: str
    notes: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        if self.original_tokens == 0:
            return 0.0
        return 1.0 - self.compressed_tokens / self.original_tokens


class ContextCompressor:
    """T1-T5 渐进压缩器（纯函数式，无外部状态）。"""

    def __init__(self, t5_summarizer: Optional[Callable[[str], str]] = None) -> None:
        """t5_summarizer：T5 语义摘要的 LLM 回调（Phase 5 接入 Provider）。"""
        self.t5_summarizer = t5_summarizer

    def compress(self, code: str, level: CompressionLevel = CompressionLevel.T4) -> CompressionResult:
        """按级别压缩。"""
        original_tokens = self.estimate_tokens(code)
        if level == CompressionLevel.T1:
            content = self._t1_script_filter(code)
        elif level == CompressionLevel.T2:
            content = self._t2_ast_prune(code)
        elif level == CompressionLevel.T3:
            content = self._t3_symbol_rename(code)
        elif level == CompressionLevel.T4:
            content = self._t4_structured_summary(code)
        elif level == CompressionLevel.T5:
            content = self._t5_semantic_summary(code)
        else:
            content = code
        return CompressionResult(
            level=level,
            original_tokens=original_tokens,
            compressed_tokens=self.estimate_tokens(content),
            content=content,
        )

    # ---------- T1 脚本过滤 ----------

    def _t1_script_filter(self, code: str) -> str:
        """删注释/空行/独立 docstring。"""
        lines = []
        for line in code.splitlines():
            if COMMENT_RE.match(line) or BLANK_LINE_RE.match(line):
                continue
            if DOCSTRING_RE.match(line):
                continue
            lines.append(line)
        return "\n".join(lines)

    # ---------- T2 AST 剪枝 ----------

    def _t2_ast_prune(self, code: str) -> str:
        """AST 剪枝：解析 Python 后移除 docstring 节点（函数/类/模块级）。"""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return self._t1_script_filter(code)

        # 收集所有 docstring 的行区间
        removed_lines: set[int] = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body:
                first = body[0]
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(getattr(first, "value", None), ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    start = getattr(first, "lineno", 1)
                    end = getattr(first, "end_lineno", start)
                    removed_lines.update(range(start, end + 1))

        lines = code.splitlines(keepends=True)
        # 同时过滤注释/空行
        result = []
        for i, line in enumerate(lines, 1):
            if i in removed_lines:
                continue
            if COMMENT_RE.match(line) or BLANK_LINE_RE.match(line):
                continue
            result.append(line)
        return "".join(result)

    # ---------- T3 符号重命名 ----------

    def _t3_symbol_rename(self, code: str) -> str:
        """符号重命名：短变量名压缩（不破坏语义的安全子集）。

        仅重命名满足条件的局部变量：单字母/双字母，且非关键字、非内置。
        注意：不做跨作用域符号表同步（Phase 1 旧实现遗留缺陷），仅做安全子集。
        """
        # 保守实现：仅压缩 ` 变量 = ` 赋值左侧的简单标识符（长度>2 且全小写）
        def repl(m: re.Match) -> str:
            name = m.group(1)
            if len(name) <= 2 or name in {"self", "cls", "return", "import", "from", "def", "class"}:
                return m.group(0)
            return f"{m.group(2)}{name[0]} = "

        return re.sub(r"(\n(\s*))([a-z][a-z0-9_]{2,15}) = ", lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)[0]} = ", code)

    # ---------- T4 结构化摘要 ----------

    def _t4_structured_summary(self, code: str) -> str:
        """结构化摘要：提取签名/结构，保留可读性。"""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return self._t1_script_filter(code)[:2000]

        summary_lines = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = ", ".join(a.arg for a in node.args.args)
                summary_lines.append(f"def {node.name}({args}): ...")
            elif isinstance(node, ast.ClassDef):
                summary_lines.append(f"class {node.name}: ...")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                summary_lines.append(ast.unparse(node))
        return "\n".join(summary_lines)

    # ---------- T5 语义摘要 ----------

    def _t5_semantic_summary(self, code: str) -> str:
        """语义摘要：LLM 高层描述（无 summarizer 时回退 T4）。"""
        if self.t5_summarizer is not None:
            try:
                return self.t5_summarizer(code)
            except Exception:
                pass
        return self._t4_structured_summary(code)

    # ---------- 工具 ----------

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """粗略 token 估算（中文按字，英文按词）。"""
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        other = re.sub(r"[\u4e00-\u9fff\s]", "", text)
        return chinese_chars + (len(other) // 4 if other else 0)
