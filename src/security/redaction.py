"""密钥脱敏。"""

from __future__ import annotations

import re
from typing import Optional

# 密钥模式：sk-xxx / Bearer xxx / api_key / token / password 等
SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"(sk-[A-Za-z0-9_\-]{8,})", "sk-***"),
    (r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", r"\1***"),
    (r"(api[_-]?key[\"'\s:=]+)[A-Za-z0-9_\-]{12,}", r"\1***"),
    (r"(token[\"'\s:=]+)[A-Za-z0-9._\-]{12,}", r"\1***"),
    (r"(password[\"'\s:=]+)[^\s\"']{6,}", r"\1***"),
    (r"(Authorization[\"'\s:=]+)[A-Za-z0-9._\-]{12,}", r"\1***"),
    (r"(secret[\"'\s:=]+)[A-Za-z0-9._\-]{12,}", r"\1***"),
    (r"(AKIA[0-9A-Z]{16})", "AKIA***"),
]


class SecretRedactor:
    """密钥脱敏器：文本/字典深度脱敏（事件写入前调用）。"""

    def __init__(self, patterns: Optional[list[tuple[str, str]]] = None) -> None:
        self._patterns = [(re.compile(p), r) for p, r in (patterns or SECRET_PATTERNS)]

    def redact_text(self, text: str) -> str:
        """文本脱敏。"""
        for pat, repl in self._patterns:
            text = pat.sub(repl, text)
        return text

    def redact(self, obj) -> object:
        """深度脱敏：字符串递归替换，dict/list 递归遍历。

        对 dict 的 (key, value) 组合处理：value 为字符串时拼接 key 上下文（如 "api_key": "xxx"），
        确保 `"api_key": "secret"` 形态能被匹配；裸字符串按通用模式脱敏。
        """
        if isinstance(obj, str):
            return self.redact_text(obj)
        if isinstance(obj, dict):
            result = {}
            for k, v in obj.items():
                if isinstance(v, str):
                    # 拼接 key 上下文提升匹配率
                    result[k] = self.redact_text(f'"{k}": "{v}"') if self._looks_secret_key(str(k)) else self.redact(v)
                    if result[k].endswith('***"') and not result[k].startswith("***"):
                        # 提取脱敏后的值部分
                        result[k] = result[k][result[k].index("***"):].rstrip('"')
                else:
                    result[k] = self.redact(v)
            return result
        if isinstance(obj, list):
            return [self.redact(i) for i in obj]
        if isinstance(obj, tuple):
            return tuple(self.redact(i) for i in obj)
        return obj

    @staticmethod
    def _looks_secret_key(key: str) -> bool:
        return any(k in key.lower() for k in ("api_key", "apikey", "token", "password", "secret", "authorization", "key"))


# 单例（事件写入统一调用）
_default_redactor = SecretRedactor()


def redact_event_content(content) -> object:
    """事件内容脱敏入口（EventLog 写入前由 loop 调用）。"""
    return _default_redactor.redact(content)
