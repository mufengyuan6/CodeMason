"""spill 私有落盘（v1.26，3.2 阶段1——对标 DSH spill，与 offload 并存）。

超大工具输出（超出 offload 预算、模型仍需完整上下文引用的）完整存**会话
作用域私有目录**，返回 locator + 检索提示给模型（agent 知道"完整内容在哪、
怎么取回"）。

安全纪律（对齐 DSH + defensive-patterns"Never hand untrusted output the
ambient environment or predictable paths"）：
- 私有目录（0700）：spill 根 + 会话子目录均 owner-only，防世界可读
- 随机文件名：不可预测（防猜测路径读取）
- wx 独占打开：预置文件存在则拒绝（防 symlink 竞争/同名抢占）
- 存储失败 → 拒绝（OSError 上抛），调用方决定降级（保留 inline 结果）

范式声明：函数式 + 薄类（存储即副作用）。
"""

from __future__ import annotations

import os
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SpillRef:
    """一次 spill 的引用（locator + 检索提示）。"""

    locator: str          # 私有文件绝对路径（模型可 Read 回读）
    bytes: int            # 落盘字节数
    retrieval_hint: str   # 模型可见的检索提示（如何取回/为何 spill）


class SpillStore:
    """会话作用域 spill 存储（0700 私有目录 + 随机名 + wx 独占打开）。"""

    def __init__(self, root: str | Path, *, spill_dir_name: str = ".spill") -> None:
        self.root = Path(root)
        self.spill_dir_name = spill_dir_name

    def _ensure_root(self) -> Path:
        """确保 spill 根目录存在（0700 权限）。"""
        spill_root = self.root / self.spill_dir_name
        spill_root.mkdir(parents=True, exist_ok=True)
        os.chmod(spill_root, 0o700)  # owner-only（防世界可读）
        return spill_root

    def _spill_dir(self, session_id: str) -> Path:
        """会话作用域子目录（0700，session id 编码防路径穿越）。"""
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in session_id)[:64] or "anon"
        d = self._ensure_root() / safe
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        return d

    def save_text(self, session_id: str, suggested_name: str, content: str) -> SpillRef:
        """持久化 content 到会话作用域私有文件，返回 SpillRef。

        文件名：随机（secrets.token_hex，不可预测）——`suggestedName` 只用于
        检索提示（人类可读），绝不直接作文件名（防路径注入/可预测路径）。
        存储失败（ENOSPC/权限/非目录）→ 抛 OSError（调用方决定降级）。
        """
        if not isinstance(content, str):
            raise TypeError("spill content must be str")

        d = self._spill_dir(session_id)
        # 随机文件名 + wx 独占打开（防 symlink 竞争：文件不存在才创建成功）
        filename = f"{secrets.token_hex(8)}.txt"
        path = d / filename
        data = content.encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        hint = (
            f"Tool output spilled to session storage ({len(data)} bytes). "
            f"Read {path} to retrieve the full content; suggested name: {suggested_name}."
        )
        return SpillRef(locator=str(path), bytes=len(data), retrieval_hint=hint)
