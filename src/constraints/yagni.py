"""YAGNI 约束引擎 = 独立确定性验证 Hook。

- 与生成解耦的 Post-validation Hook（非提示词，对标 SonarQube 规则引擎架构）
- 七级决策阶梯：L1 真需要吗（LLM 软判断）→ L2 库里有吗（AST 相似度查重复）→ L3 标准库能吗（API 匹配表）→ L4 平台原生吗 → L5 现有依赖覆盖吗（依赖图）→ L6 能一行吗 → L7 写最少代码
- 硬规则（L2-L6 机械化）+ 软规则（L1 语义判断）——只对硬规则承诺量化指标
- 四维量化报告（G2）：行数减少 + 依赖未新增 + 重复实现数 + 可读性守门
- 作用于 staging diff（G11）：Hook 拦截 = staging 移除，零回滚成本
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Optional

from ..parser import get_parser_for_file


@dataclass
class YagniFinding:
    """一条 YAGNI 检出。"""

    rule: str  # L2-L6
    level: int
    file: str
    line: int = 0
    message: str = ""
    severity: str = "warn"  # info / warn / block


@dataclass
class YagniReport:
    """四维量化报告（G2）。"""

    lines_reduced: int = 0
    deps_added: int = 0
    duplicates_found: int = 0
    readability_ok: bool = True
    findings: list[YagniFinding] = field(default_factory=list)
    blocked: bool = False

    def to_dict(self) -> dict:
        return {
            "lines_reduced": self.lines_reduced,
            "deps_added": self.deps_added,
            "duplicates_found": self.duplicates_found,
            "readability_ok": self.readability_ok,
            "blocked": self.blocked,
            "findings": [{"rule": f.rule, "level": f.level, "file": f.file, "line": f.line, "message": f.message, "severity": f.severity} for f in self.findings],
        }


# 标准库替代表（L3：能否用原生）
STDLIB_SUBSTITUTES: list[tuple[str, str]] = [
    ("numpy.mean(", "statistics.fmean("),
    ("pandas.read_csv", "csv.reader"),
    ("requests.get", "urllib.request.urlopen"),
    ("json.loads", "json.loads"),  # 本身就是标准库
    ("os.path.exists", "os.path.exists"),
]

# 平台原生替代表（L4）
PLATFORM_SUBSTITUTES: list[tuple[str, str]] = [
    ("subprocess.run(['ls'", "os.listdir"),
    ("subprocess.run(['cat'", "open()"),
    ("subprocess.run(['grep'", "pathlib + 内建查找"),
]


class YagniEngine:
    """YAGNI 独立确定性验证 Hook：对 staging diff 做静态分析。"""

    def __init__(self, max_complexity: int = 15, max_nesting: int = 4, max_name_len: int = 60) -> None:
        self.max_complexity = max_complexity
        self.max_nesting = max_nesting
        self.max_name_len = max_name_len

    # ---------- Hook 入口 ----------

    def validate(self, old_content: str, new_content: str, file_path: str) -> YagniReport:
        """对 staging diff（old → new）执行 YAGNI 验证。返回四维报告。"""
        report = YagniReport()
        self._check_duplicates(new_content, file_path, report)      # L2
        self._check_stdlib(new_content, file_path, report)          # L3
        self._check_platform(new_content, file_path, report)        # L4
        self._check_unused_deps(new_content, file_path, report)     # L5
        self._check_one_liner(new_content, file_path, report)       # L6
        self._check_readability(new_content, file_path, report)     # 可读性守门
        # 行数减少（L7 写最少代码的量化）
        old_lines = len(old_content.splitlines())
        new_lines = len(new_content.splitlines())
        report.lines_reduced = max(0, old_lines - new_lines)
        report.blocked = any(f.severity == "block" for f in report.findings)
        return report

    def as_hook(self):
        """转为 StagingSandbox Hook（G11：作用在 staging 上）。"""

        def hook(change) -> dict:
            report = self.validate(change.old_content, change.new_content, change.path)
            return {
                "hook": "yagni",
                "blocked": report.blocked,
                "reason": report.to_dict(),
            }

        return hook

    # ---------- L2 库里有吗（AST 相似度查重复） ----------

    def _check_duplicates(self, code: str, file_path: str, report: YagniReport) -> None:
        """AST 相似度检测：同一模式出现 ≥2 次 → 重复实现。"""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return
        pattern_counts: dict[str, int] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Call, ast.Assign)):
                key = ast.dump(node, include_attributes=False)[:200]
                pattern_counts[key] = pattern_counts.get(key, 0) + 1
        for key, count in pattern_counts.items():
            if count >= 3:  # 同一 AST 结构出现 3 次
                report.duplicates_found += 1
                report.findings.append(
                    YagniFinding(rule="L2", level=2, file=file_path, message=f"重复实现模式出现 {count} 次（AST 相似度），建议提取或复用库", severity="warn")
                )

    # ---------- L3 标准库能吗（API 匹配表） ----------

    def _check_stdlib(self, code: str, file_path: str, report: YagniReport) -> None:
        for third_party, stdlib in STDLIB_SUBSTITUTES:
            if third_party in code and third_party != stdlib:
                report.findings.append(
                    YagniFinding(rule="L3", level=3, file=file_path, message=f"可用标准库替代: {third_party} → {stdlib}", severity="warn")
                )

    # ---------- L4 平台原生吗 ----------

    def _check_platform(self, code: str, file_path: str, report: YagniReport) -> None:
        for subprocess_call, native in PLATFORM_SUBSTITUTES:
            if subprocess_call in code:
                report.findings.append(
                    YagniFinding(rule="L4", level=4, file=file_path, message=f"可用平台原生替代: {subprocess_call} → {native}", severity="warn")
                )

    # ---------- L5 现有依赖覆盖吗（依赖图） ----------

    def _check_unused_deps(self, code: str, file_path: str, report: YagniReport) -> None:
        """检测：import 了但未使用的依赖。"""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        for name in imported:
            base = name.split(".")[0]
            # 粗略判断是否被使用（排除 import 行本身）
            usage = code.count(base)
            if usage <= 1:  # 只在 import 行出现
                report.findings.append(
                    YagniFinding(rule="L5", level=5, file=file_path, message=f"未使用依赖: {name}", severity="info")
                )

    # ---------- L6 能一行吗 ----------

    ONE_LINER_PATTERNS = [
        (r"for\s+\w+\s+in\s+\w+:\s*\n\s*print\(", "循环内 print 可用一行生成式"),
        (r"result\s*=\s*\[\]\s*\n\s*for\s+", "空列表+循环追加可用列表推导式"),
        (r"if\s+.+:\s*\n\s+return\s+True\s*\n\s+else:\s*\n\s+return\s+False", "if/else 返回布尔可用一行表达式"),
    ]

    def _check_one_liner(self, code: str, file_path: str, report: YagniReport) -> None:
        for pattern, desc in self.ONE_LINER_PATTERNS:
            if re.search(pattern, code):
                report.findings.append(
                    YagniFinding(rule="L6", level=6, file=file_path, message=f"可压缩为一行: {desc}", severity="info")
                )

    # ---------- 可读性守门（G2：单函数级守门） ----------

    def _check_readability(self, code: str, file_path: str, report: YagniReport) -> None:
        """圈复杂度 + 嵌套深度 + 命名长度守门。"""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                complexity = self._cyclomatic_complexity(node)
                if complexity > self.max_complexity:
                    report.readability_ok = False
                    report.findings.append(
                        YagniFinding(rule="L7", level=7, file=file_path, line=node.lineno, message=f"圈复杂度 {complexity} 超阈值 {self.max_complexity}", severity="block")
                    )
                if len(node.name) > self.max_name_len:
                    report.readability_ok = False
                    report.findings.append(
                        YagniFinding(rule="L7", level=7, file=file_path, line=node.lineno, message=f"函数名过长: {node.name[:40]}...", severity="warn")
                    )

    @staticmethod
    def _cyclomatic_complexity(node: ast.AST) -> int:
        """McCabe 圈复杂度：分支节点数 + 1。"""
        branches = 0
        for n in ast.walk(node):
            if isinstance(n, (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.With, ast.AsyncFor, ast.AsyncWith, ast.comprehension, ast.BoolOp)):
                branches += 1
            if isinstance(n, ast.BoolOp):
                branches += len(n.values) - 1
        return branches + 1
