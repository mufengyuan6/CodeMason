"""凭据独立通道（G16③ v1.23 落地：凭据独立通道）。

设计（design.md G16③）：
- API Key 等凭据独立存储（credentials.yaml / 环境变量 / .env），事件日志只存引用
  （credential_id）无明文
- 事件写前清洗（scanner 扫描疑似凭据模式替换为引用）——审计安全
- 凭据轮换不改事件流（事件流只引用 credential_id，改 yaml 即生效）

范式声明：业务逻辑层 OOP（class-based Service）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from .redaction import SecretRedactor


class CredentialStore:
    """凭据存储：独立文件 + 环境变量，对外只暴露引用。

    credentials.yaml 结构（零依赖解析：`section.key: value` 平面格式，兼容 yaml 子集）：
        api_keys.xfyun: "sk-xxx"
        api_keys.openai: "sk-yyy"
        tokens.github: "ghp_xxx"

    引用格式：`{{credential:api_keys.xfyun}}`——事件流只存引用，不存明文。
    """

    REF_PATTERN = re.compile(r"\{\{credential:([\w.]+)\}\}")

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = Path(path) if path else Path.home() / ".codemason" / "credentials.yaml"
        self._store: dict = {}
        self._redactor = SecretRedactor()
        self._load()

    def _load(self) -> None:
        """从凭据文件 + 环境变量加载（文件优先）。"""
        if self.path.exists():
            try:
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or ":" not in line:
                        continue
                    key, _, val = line.partition(":")
                    key = key.strip().strip('"').strip("'")
                    val = val.strip().strip('"').strip("'")
                    if key and val:
                        self._set_nested(key, val)
            except Exception:
                self._store = {}
        # 环境变量兜底（CI/容器无文件时）
        env_mapping = {
            "api_keys.xfyun": os.environ.get("XFYUN_API_KEY"),
            "api_keys.openai": os.environ.get("OPENAI_API_KEY"),
            "api_keys.deepseek": os.environ.get("DEEPSEEK_API_KEY"),
        }
        for key, val in env_mapping.items():
            if val and not self.get(key):
                self._set_nested(key, val)

    def _set_nested(self, dotted_key: str, value: str) -> None:
        parts = dotted_key.split(".")
        node = self._store
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value

    def get(self, dotted_key: str) -> Optional[str]:
        """按点路径取凭据（api_keys.xfyun）。"""
        node = self._store
        for p in dotted_key.split("."):
            if not isinstance(node, dict) or p not in node:
                return None
            node = node[p]
        return node if isinstance(node, str) else None

    def set(self, dotted_key: str, value: str) -> None:
        """写入凭据（持久化到文件，权限收紧）。"""
        self._set_nested(dotted_key, value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{k}: {v}" for k, v in self._flatten(self._store)]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)  # 仅所有者可读写
        except OSError:
            pass

    @staticmethod
    def _flatten(node: dict, prefix: str = "") -> list[tuple[str, str]]:
        out = []
        for k, v in node.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                out.extend(CredentialStore._flatten(v, key))
            else:
                out.append((key, str(v)))
        return out

    def reference(self, dotted_key: str) -> str:
        """生成引用（事件流只存这个）。"""
        return f"{{{{credential:{dotted_key}}}}}"

    def resolve(self, text: str) -> str:
        """把引用解析回明文（工具执行时调用，绝不在日志中出现明文）。"""
        def _repl(m: re.Match) -> str:
            val = self.get(m.group(1))
            return val if val is not None else m.group(0)  # 找不到引用保留原样（fail-safe）

        return self.REF_PATTERN.sub(_repl, text)

    def scrub(self, content: object) -> object:
        """事件写前清洗：明文凭据 → 引用替代（审计安全，日志零明文）。"""
        return self._redactor.redact(content)

    def has_plaintext(self, text: str) -> bool:
        """检查文本是否含疑似明文凭据（审计测试用）。"""
        redacted = self._redactor.redact_text(text)
        return redacted != text


def scrub_event_content(content: object, store: Optional[CredentialStore] = None) -> object:
    """事件写前清洗入口（EventLog 写入前调用）。

    store 未提供时用默认 redactor（正则脱敏）；提供 store 时增加引用语义（更强）。
    """
    if store is None:
        from .redaction import redact_event_content

        return redact_event_content(content)
    return store.scrub(content)
