"""Provider 抽象层。

- 讯飞为默认 Provider，OpenAI 兼容接口（opencode.ai 等）可插拔
- 不绑定单一厂商（2026 年 Roo 归档/Gemini 退役已证明生态剧变，绑定即风险）
- 熔断降级：
  - 指数退避重试（必做）：失败后冷却 30s → 60s → 120s，最多 3 次
  - 模型 fallback 链（必做）：同角色内降级，不跨角色
  - 多 key 轮换（接口能力，非当前实现）：Provider 接口定义 key 池
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import httpx


class ProviderError(Exception):
    """Provider 调用错误。"""


class ProviderRateLimited(ProviderError):
    """限流/过载错误（触发指数退避）。"""


@dataclass
class ProviderConfig:
    """Provider 配置（多 key 留作接口：keys 列表，当前取第一个）。"""

    name: str
    base_url: str
    api_key: str
    default_model: str
    keys: list[str] = field(default_factory=list)
    timeout: float = 120.0

    @property
    def active_key(self) -> str:
        return self.api_key or (self.keys[0] if self.keys else "")


class BaseProvider(ABC):
    """Provider 抽象基类：chat 补全 + 指数退避重试 + 同角色降级。"""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self._client = httpx.Client(timeout=config.timeout)
        self._retry_backoff = [30, 60, 120]  # 指数退避冷却（秒）

    @abstractmethod
    def chat(self, messages: list[dict], *, model: Optional[str] = None, temperature: float = 0.2) -> str:
        """OpenAI 兼容 chat 补全。"""

    def generate(self, messages: list[dict], *, role: str = "editor", model: Optional[str] = None) -> str:
        """带重试的生成（role 决定降级链，模型由 router 传入）。"""
        self.calls.append({"role": role, "model": model, "messages": messages})
        last_error: Optional[Exception] = None
        for attempt in range(len(self._retry_backoff) + 1):
            try:
                return self.chat(messages, model=model, temperature=0.2 if role == "editor" else 0.0)
            except ProviderRateLimited as e:
                last_error = e
                if attempt < len(self._retry_backoff):
                    wait = self._retry_backoff[attempt]
                    time.sleep(wait)
            except ProviderError as e:
                last_error = e
                break  # 非限流错误不重试（如 400/401），由 router 降级
            except Exception as e:
                last_error = e
                if attempt < len(self._retry_backoff):
                    time.sleep(self._retry_backoff[attempt])
        raise ProviderError(f"Provider {self.config.name} 调用失败: {last_error}")

    def close(self) -> None:
        self._client.close()


class OpenAICompatProvider(BaseProvider):
    """OpenAI 兼容 Provider（讯飞 / opencode.ai / 本地 Ollama 通用）。"""

    def chat(self, messages: list[dict], *, model: Optional[str] = None, temperature: float = 0.2) -> str:
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": model or self.config.default_model,
            "messages": messages,
            "temperature": temperature,
        }
        try:
            resp = self._client.post(url, headers={"Authorization": f"Bearer {self.config.active_key}"}, json=payload)
        except httpx.TimeoutException as e:
            raise ProviderRateLimited(f"请求超时: {e}") from e
        except httpx.HTTPError as e:
            raise ProviderError(f"网络错误: {e}") from e
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderRateLimited(f"HTTP {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise ProviderError(f"响应解析失败: {e}") from e


class MockProvider(BaseProvider):
    """测试用 Mock Provider（确定性，不触网）。"""

    def __init__(self, config: Optional[ProviderConfig] = None, reply: str = "mock 回复") -> None:
        super().__init__(config or ProviderConfig(name="mock", base_url="http://mock", api_key="", default_model="mock"))
        self.reply = reply
        self.calls: list[dict] = []

    def chat(self, messages: list[dict], *, model: Optional[str] = None, temperature: float = 0.2) -> str:
        self.calls.append({"model": model, "messages": messages})
        return self.reply
