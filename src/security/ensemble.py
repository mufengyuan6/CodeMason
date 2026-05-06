"""纵深防御第二道：ensemble 分析。

第一道：ShellGuard 黑名单硬锁（确定性拦截已知危险）
第二道：ensemble = 静态 AST 解析 + 规则投票（拦截未知危险）
"""

from __future__ import annotations

import ast
import re
import shlex
from typing import Optional

from .guard import ShellGuard


class StaticAnalyzer:
    """静态 AST 解析器：命令 → AST 结构 → 危险信号。"""

    # 危险模式：网络外联 / 读取密钥 / 管道执行 / 写入系统路径
    DANGER_SIGNALS = [
        (r"wget\s+http", "网络下载"),
        (r"curl\s+http", "网络请求"),
        (r"cat\s+.*(\.ssh|\.aws|\.env|credentials)", "读取密钥"),
        (r"(\.ssh|\.aws)/.*\bcat\b", "读取密钥目录"),
        (r"\bexport\s+\w+=\S+", "设置环境变量"),
        (r">\s*(/etc|/usr|C:\\Windows|C:\\\\Windows)", "写入系统路径"),
        (r"\bkubectl\s+delete", "kubectl 删除"),
        (r"\bdocker\s+(rm|kill|system)", "docker 破坏操作"),
    ]

    def analyze(self, command: str) -> list[dict]:
        """静态信号提取（AST 解析 + 模式匹配）。"""
        signals = []
        # 尝试 shlex 分词（命令结构解析）
        try:
            tokens = shlex.split(command)
            signals.append({"analyzer": "ast_tokens", "tokens": tokens[:20], "risk": "info"})
        except ValueError:
            signals.append({"analyzer": "ast_tokens", "error": "命令无法分词", "risk": "warn"})
        # 危险信号匹配
        for pattern, desc in self.DANGER_SIGNALS:
            if re.search(pattern, command, re.IGNORECASE):
                signals.append({"analyzer": "pattern", "signal": desc, "risk": "warn"})
        return signals


class LlmAnalyzer:
    """LLM 辅助分析器（可插拔；无 LLM 时降级为规则分析）。"""

    def __init__(self, llm: Optional[object] = None) -> None:
        self.llm = llm

    def analyze(self, command: str) -> dict:
        if self.llm is None:
            return {"analyzer": "llm", "risk": "unknown", "note": "无 LLM，跳过"}
        # 未来：调 LLM 判断命令意图（Phase 5 接入）
        return {"analyzer": "llm", "risk": "unknown", "note": "LLM 分析未启用"}


class EnsembleAnalyzer:
    """多分析器投票：静态 AST + 模式 + LLM（可选）。"""

    def __init__(self, static: Optional[StaticAnalyzer] = None, llm: Optional[LlmAnalyzer] = None, guard: Optional[ShellGuard] = None) -> None:
        self.static = static or StaticAnalyzer()
        self.llm = llm or LlmAnalyzer()
        self.guard = guard or ShellGuard()

    def analyze(self, command: str) -> dict:
        """完整分析：黑名单硬锁 + ensemble 投票。"""
        guard_result = self.guard.check(command)
        if guard_result["blocked"]:
            return {
                "blocked": True,
                "risk_level": "red",
                "reason": f"黑名单硬锁: {guard_result['reason']}",
                "votes": [guard_result],
            }

        static_signals = self.static.analyze(command)
        llm_vote = self.llm.analyze(command)
        votes = [{"analyzer": "static", "signals": static_signals}, llm_vote]

        # 投票：任何 static warn 信号 → yellow；无信号 → green
        warn_count = sum(1 for s in static_signals if s.get("risk") == "warn")
        if warn_count >= 2:
            return {"blocked": False, "risk_level": "yellow", "reason": f"ensemble 检出 {warn_count} 个危险信号", "votes": votes}
        if warn_count == 1:
            return {"blocked": False, "risk_level": "yellow", "reason": "ensemble 检出 1 个危险信号", "votes": votes}
        return {"blocked": False, "risk_level": "green", "reason": "ensemble 无危险信号", "votes": votes}
