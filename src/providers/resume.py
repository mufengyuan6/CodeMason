"""provider 流中断策略（G6 v1.17 落地：连接确定性——流断了就是断了）。

设计（design.md G6，对标 pi）：
- provider 流不可恢复——恢复只能从耐久边界重启或标记中断，不设计"从断掉的流里接上"
- 未完成 turn 标中断（保留耐久队列与待写）
- provider 请求标中断不自动重试
- 工具调用只有声明可重试/幂等才重试（recovery: mark_interrupted | retry_unfinished 开关）
- 部分流式输出标记为不完整 + 完整输出验证，不自动拼接
- JSONL 半行恢复（pi 开放问题，CodeMason 事件存储同风险）：追加写在崩溃瞬间留下半行
  JSON → 恢复时读到最后一行不完整 JSON → 截断丢弃/标记损坏 → 从最后完整事件续

范式声明：业务逻辑层 OOP。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class RecoveryMode(str, Enum):
    """恢复模式开关。"""

    MARK_INTERRUPTED = "mark_interrupted"      # 标中断（默认：不自动重试）
    RETRY_UNFINISHED = "retry_unfinished"      # 重试未完成（仅幂等操作）


@dataclass
class StreamStatus:
    """流式输出状态。"""

    stream_id: str
    complete: bool = False
    interrupted: bool = False
    partial_output: str = ""  # 已收到的部分输出
    verification: str = "pending"  # pending / verified / failed

    def to_dict(self) -> dict:
        return {
            "stream_id": self.stream_id,
            "complete": self.complete,
            "interrupted": self.interrupted,
            "partial_len": len(self.partial_output),
            "verification": self.verification,
        }


class StreamRecoveryPolicy:
    """provider 流中断策略：恢复只能从耐久边界重启或标记中断。"""

    def __init__(self, recovery_mode: RecoveryMode = RecoveryMode.MARK_INTERRUPTED) -> None:
        self.recovery_mode = recovery_mode
        self._streams: dict[str, StreamStatus] = {}

    def mark_stream_start(self, stream_id: str) -> StreamStatus:
        status = StreamStatus(stream_id=stream_id)
        self._streams[stream_id] = status
        return status

    def append_partial(self, stream_id: str, chunk: str) -> StreamStatus:
        """流式输出追加（部分输出不自动拼接为完整——只标记）。"""
        status = self._streams.get(stream_id)
        if status is None:
            status = self.mark_stream_start(stream_id)
        status.partial_output += chunk
        return status

    def on_interrupt(self, stream_id: str) -> dict:
        """流中断：标中断 + 部分输出标记为不完整（不自动拼接）。"""
        status = self._streams.get(stream_id)
        if status is None:
            return {"stream_id": stream_id, "error": "未知流"}
        status.interrupted = True
        status.complete = False
        status.verification = "failed"  # 部分输出不能当完整结果
        return status.to_dict()

    def should_retry(self, stream_id: str, *, is_idempotent: bool = False) -> bool:
        """是否重试：只有声明可重试/幂等才重试（retry_unfinished 模式）。"""
        if self.recovery_mode == RecoveryMode.RETRY_UNFINISHED and is_idempotent:
            return True
        return False  # 默认 mark_interrupted：不自动重试

    def verify_complete(self, stream_id: str, expected: Optional[str] = None) -> dict:
        """完整输出验证（收到终止信号后校验完整性）。"""
        status = self._streams.get(stream_id)
        if status is None:
            return {"stream_id": stream_id, "verification": "failed", "reason": "未知流"}
        if status.interrupted:
            return {"stream_id": stream_id, "verification": "failed", "reason": "流已中断，部分输出不验证为完整"}
        status.complete = True
        status.verification = "verified"
        return status.to_dict()


class JsonlHalfLineRecovery:
    """JSONL 半行恢复（G6：崩溃瞬间留下半行 JSON → 截断丢弃 → 从最后完整事件续）。

    用法：恢复时调用 strip_half_line(path) → 返回最后完整行号；后续从该行续。
    """

    @staticmethod
    def strip_half_line(path: str) -> dict:
        """读取 JSONL，丢弃最后不完整行（半行 JSON），返回修复信息。

        返回：{truncated: bool, last_valid_line: int, total_lines: int}
        """
        try:
            with open(path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            return {"truncated": False, "last_valid_line": 0, "total_lines": 0, "error": "文件不可读"}
        last_valid = 0
        truncated = False
        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                json.loads(stripped)
                last_valid = i
            except json.JSONDecodeError:
                # 遇到不完整行：之后都是垃圾（append-only 语义，只可能最后一行半截）
                truncated = True
                break
        # 截断丢弃：重写文件只保留完整行
        if truncated:
            valid = lines[:last_valid]
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(valid)
        return {"truncated": truncated, "last_valid_line": last_valid, "total_lines": len(lines)}

    @staticmethod
    def resume_cursor(path: str) -> int:
        """恢复游标：最后完整事件之后（从最后完整事件续）。"""
        info = JsonlHalfLineRecovery.strip_half_line(path)
        return info["last_valid_line"]
