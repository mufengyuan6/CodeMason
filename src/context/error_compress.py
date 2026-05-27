"""错误/验证场景结构化压缩（3.2 阶段1，对标 MCP-Rubber-Duck，P1）。

debug 是 coding agent 最贵场景，入口即结构化：
- **报错堆栈**：只提取根因帧 + 相关文件行（过滤框架内部帧/无关日志）
- **测试输出**：只保留 failed/error 行 + 差异（diff 摘要）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# 常见框架内部帧标记（过滤，减少噪音）
FRAMEWORK_FRAME_MARKERS = (
    "site-packages",
    "dist-packages",
    "venv/",
    ".venv/",
    "pytest/",
    "unittest/",
    "fastapi/",
    "starlette/",
    "uvicorn/",
    "pydantic/",
)


@dataclass
class ErrorCompressResult:
    """错误压缩结果。"""

    root_cause_frames: list[str] = field(default_factory=list)  # 根因帧
    related_lines: list[str] = field(default_factory=list)      # 相关文件行
    original_lines: int = 0
    compressed_lines: int = 0

    @property
    def ratio(self) -> float:
        return self.compressed_lines / self.original_lines if self.original_lines else 1.0


@dataclass
class TestOutputCompressResult:
    """测试输出压缩结果。"""

    failed_lines: list[str] = field(default_factory=list)
    error_lines: list[str] = field(default_factory=list)
    diff_lines: list[str] = field(default_factory=list)
    original_lines: int = 0
    compressed_lines: int = 0

    @property
    def ratio(self) -> float:
        return self.compressed_lines / self.original_lines if self.original_lines else 1.0


class ErrorCompressor:
    """错误/验证场景结构化压缩器。"""

    def compress_traceback(self, traceback_text: str, *, max_frames: int = 5) -> ErrorCompressResult:
        """报错堆栈压缩：只取根因帧 + 相关文件行。

        策略：
        - 跳过框架内部帧（site-packages/pytest 等）
        - 保留"File ... line ... in ..."应用代码帧（根因帧）
        - 保留最后的错误消息行（Traceback 末尾的 Exception: msg）
        """
        lines = traceback_text.splitlines()
        app_frames: list[str] = []
        error_msg: list[str] = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            # 错误消息行（Traceback 末尾：异常类型 + 消息）
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+: ", stripped):
                error_msg.append(stripped)
                continue
            if "Error" in stripped and ":" in stripped and not stripped.startswith("File"):
                error_msg.append(stripped)
                continue
            # 栈帧行
            if "File " in line and " line " in line:
                if any(m in line for m in FRAMEWORK_FRAME_MARKERS):
                    continue  # 框架内部帧过滤
                app_frames.append(line.strip())
        # 根因帧 = 应用代码帧（最多 max_frames 条）+ 错误消息
        frames = app_frames[:max_frames]
        result_lines = frames + error_msg[:2]
        return ErrorCompressResult(
            root_cause_frames=frames,
            related_lines=error_msg,
            original_lines=len(lines),
            compressed_lines=len(result_lines),
        )

    def compress_test_output(self, output_text: str, *, max_diff: int = 20) -> TestOutputCompressResult:
        """测试输出压缩：只保留 failed/error 行 + diff 摘要。"""
        lines = output_text.splitlines()
        failed: list[str] = []
        errors: list[str] = []
        diff: list[str] = []
        in_diff = False
        for line in lines:
            stripped = line.strip()
            low = stripped.lower()
            if re.match(r"^(FAILED|F) ", stripped) or "failed" in low[:40]:
                failed.append(stripped)
            elif re.match(r"^(ERROR|E) ", stripped) or low.startswith("error"):
                errors.append(stripped)
            # diff 摘要（unified diff：+/- 行，但限制数量）
            if stripped.startswith(("+", "-")) and not stripped.startswith(("+++", "---")):
                if not in_diff:
                    diff.append("--- diff start ---")
                    in_diff = True
                if len(diff) <= max_diff:
                    diff.append(stripped[:160])
            elif in_diff:
                diff.append("--- diff end ---")
                in_diff = False
        if diff and diff[-1] != "--- diff end ---":
            diff.append("--- diff end ---")
        return TestOutputCompressResult(
            failed_lines=failed[:20],
            error_lines=errors[:10],
            diff_lines=diff,
            original_lines=len(lines),
            compressed_lines=len(failed[:20]) + len(errors[:10]) + len(diff),
        )
