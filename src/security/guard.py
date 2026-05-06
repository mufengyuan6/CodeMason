"""shell 黑名单硬锁。"""

from __future__ import annotations

import re
from typing import Optional

# 确定性危险模式（硬锁，不依赖模型判断）
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)*/", "rm 递归删除根路径"),
    (r"\brm\s+-rf\s+~", "rm -rf 家目录"),
    (r"\bmkfs(?:\s|\.)", "格式化文件系统"),
    (r"\bdd\s+if=.*\s+of=/dev/", "dd 写入块设备"),
    (r"\bsudo\s+", "sudo 提权"),
    (r"\bsed\s+-i", "sed -i 原地修改（需审批）"),
    (r"\bmv\s+.*\s+/dev/null", "mv 到 /dev/null（破坏性）"),
    (r":\(\)\s*\{", "fork 炸弹"),
    (r">\s*/dev/sd[a-z]", "写入磁盘设备"),
    (r"\bchmod\s+-R\s+777", "chmod -R 777 权限放大"),
    (r"\bgit\s+push\s+(-f|--force)", "git push -f 强推"),
    (r"\bgit\s+reset\s+--hard", "git reset --hard"),
    (r"\bcurl\s+.*\|\s*(ba|sh)\s*$", "curl 管道执行（供应链风险）"),
]

# 已知安全白名单（即使命中可疑模式也放行，避免误杀）
SAFE_OVERRIDES: list[tuple[str, str]] = [
    (r"rm\s+-rf\s+(/tmp|/var/tmp)/[a-zA-Z0-9_./-]+", "清理临时目录"),
]


class ShellGuard:
    """shell 命令黑名单硬锁：拦截判定 + 风险等级。"""

    def __init__(self, patterns: Optional[list[tuple[str, str]]] = None) -> None:
        self.patterns = patterns or DANGEROUS_PATTERNS
        self._compiled = [(re.compile(p), desc) for p, desc in self.patterns]
        self._safe = [(re.compile(p), desc) for p, desc in SAFE_OVERRIDES]

    def check(self, command: str) -> dict:
        """检查命令。返回 {blocked, reason, risk_level}。"""
        for safe_pat, _ in self._safe:
            if safe_pat.search(command):
                return {"blocked": False, "reason": None, "risk_level": "yellow"}
        for pat, desc in self._compiled:
            if pat.search(command):
                return {"blocked": True, "reason": desc, "risk_level": "red"}
        # 涉及文件写入/网络请求的常见命令标黄（需审批）
        if re.search(r"\b(wget|curl|pip\s+install|npm\s+install)\b", command):
            return {"blocked": False, "reason": None, "risk_level": "yellow"}
        return {"blocked": False, "reason": None, "risk_level": "green"}

    def analyze(self, command: str) -> dict:
        """ensemble 第一票：黑名单硬锁（确定性）。"""
        return self.check(command)
