"""Provider 层：抽象基类 + OpenAI 兼容实现 + Mock + 智能重试 + 流中断恢复。"""

from .base import (
    BaseProvider,
    MockProvider,
    OpenAICompatProvider,
    ProviderConfig,
    ProviderError,
    ProviderRateLimited,
)
from .resume import JsonlHalfLineRecovery, RecoveryMode, StreamRecoveryPolicy, StreamStatus
from .retry import CircuitBreaker, ErrorClass, RetryEngine, RetryPolicy, RetryResult

__all__ = [
    "BaseProvider",
    "OpenAICompatProvider",
    "MockProvider",
    "ProviderConfig",
    "ProviderError",
    "ProviderRateLimited",
    "RetryEngine",
    "RetryPolicy",
    "RetryResult",
    "ErrorClass",
    "CircuitBreaker",
    "StreamRecoveryPolicy",
    "RecoveryMode",
    "StreamStatus",
    "JsonlHalfLineRecovery",
]
