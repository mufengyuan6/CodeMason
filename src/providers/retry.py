"""智能重试引擎（G6 v1.17 落地：连接确定性——错误分类学 + 时间预算驱动）。

设计（design.md G6）：
- 错误分类学：瞬时错误（429/500/502/503/529/网络超时/连接重置/408 模糊）→ 值得重试；
  持久错误（400/401/403/404/余额不足/模型不存在）→ 立即失败不重试（重试一万次也一样且烧配额）
- 指数退避 + full jitter（min(max_delay, base×2^attempt) + 随机抖动，防 thundering herd）
- 尊重 Retry-After 头（Anthropic 429 返回等待时长，无视会被限流更狠）
- 时间预算驱动（stop_after_delay 而非 stop_after_attempt："在这段窗口内反复尝试直到
  成功或时间到"）
- 熔断器（连续失败快速失败 + 周期探测恢复）
- fallback 链（同角色降级 → 跨 provider 切换）
- 端到端幂等（工具调用带稳定 ID + 可重试性声明）

范式声明：业务逻辑层 OOP。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class ErrorClass(str, Enum):
    """错误分类学（G6：瞬时 vs 持久）。"""

    TRANSIENT = "transient"      # 值得重试（429/5xx/网络/超时/408）
    PERSISTENT = "persistent"    # 立即失败（400/401/403/404/余额不足）
    AMBIGUOUS = "ambiguous"      # 模糊（408 等，可试一次）


# 状态码 → 错误分类（默认表，per-provider 可覆盖）
STATUS_CLASSIFY: dict[int, ErrorClass] = {
    400: ErrorClass.PERSISTENT, 401: ErrorClass.PERSISTENT, 403: ErrorClass.PERSISTENT,
    404: ErrorClass.PERSISTENT, 405: ErrorClass.PERSISTENT,
    408: ErrorClass.AMBIGUOUS,
    429: ErrorClass.TRANSIENT,
    500: ErrorClass.TRANSIENT, 502: ErrorClass.TRANSIENT, 503: ErrorClass.TRANSIENT, 529: ErrorClass.TRANSIENT,
}


class RetryPolicy:
    """重试策略：错误分类 + 退避参数 + 时间预算。"""

    def __init__(self, *, base_delay: float = 1.0, max_delay: float = 60.0, max_retries: int = 5, time_budget: Optional[float] = None) -> None:
        """time_budget（秒）：时间预算驱动（None = 按 max_retries 次数）。"""
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.time_budget = time_budget  # 时间预算驱动（stop_after_delay）

    def classify(self, error: Exception, status_code: Optional[int] = None) -> ErrorClass:
        """错误分类（per-provider 适配器可覆盖）。"""
        if status_code is not None:
            return STATUS_CLASSIFY.get(status_code, ErrorClass.AMBIGUOUS)
        # 按异常类型推断
        name = type(error).__name__.lower()
        if any(k in name for k in ("timeout", "ratelimit", "rate", "overloaded", "connect", "reset", "network", "temporary", "server")):
            return ErrorClass.TRANSIENT
        if any(k in name for k in ("auth", "forbidden", "notfound", "invalid", "badrequest", "balance", "quota")):
            return ErrorClass.PERSISTENT
        return ErrorClass.AMBIGUOUS

    def delay_for(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """指数退避 + full jitter（尊重 Retry-After 头）。"""
        if retry_after is not None and retry_after > 0:
            return retry_after  # 服务端指定等待时长
        exp = min(self.max_delay, self.base_delay * (2 ** attempt))
        return random.uniform(0, exp)  # full jitter（OpenAI Cookbook 推荐）

    def budget_exhausted(self, start_ts: float) -> bool:
        """时间预算是否耗尽（stop_after_delay）。"""
        if self.time_budget is None:
            return False
        return (time.time() - start_ts) >= self.time_budget


class CircuitBreaker:
    """熔断器：连续失败快速失败 + 周期探测恢复。"""

    def __init__(self, *, failure_threshold: int = 5, recovery_timeout: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._failures = 0
        self._open_since: Optional[float] = None

    @property
    def is_open(self) -> bool:
        """熔断是否打开（拒绝请求，快速失败）。"""
        if self._open_since is None:
            return False
        # 周期探测恢复：到恢复窗口尝试 half-open
        if (time.time() - self._open_since) >= self.recovery_timeout:
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._open_since = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open_since = time.time()

    def reset(self) -> None:
        self._failures = 0
        self._open_since = None


@dataclass
class RetryResult:
    """重试执行结果。"""

    ok: bool
    result: Optional[object] = None
    attempts: int = 0
    error: Optional[str] = None
    error_class: Optional[str] = None
    exhausted_budget: bool = False
    circuit_open: bool = False

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "attempts": self.attempts, "error": self.error,
            "error_class": self.error_class, "exhausted_budget": self.exhausted_budget, "circuit_open": self.circuit_open,
        }


class RetryEngine:
    """智能重试引擎（G6 核心：错误分类 + 退避 + 时间预算 + 熔断）。"""

    def __init__(self, policy: Optional[RetryPolicy] = None, breaker: Optional[CircuitBreaker] = None) -> None:
        self.policy = policy or RetryPolicy()
        self.breaker = breaker or CircuitBreaker()
        self._history: list[dict] = []

    def execute(
        self,
        fn: Callable,
        *,
        classify_hint: Optional[int] = None,
        on_retry: Optional[Callable[[int, float, Exception], None]] = None,
    ) -> RetryResult:
        """执行带智能重试的调用。

        fn: 可调用（返回结果）；若抛异常，按错误分类决定重试/放弃。
        classify_hint: 状态码提示（429/5xx 等）。
        """
        start = time.time()
        if self.breaker.is_open:
            return RetryResult(ok=False, error="熔断器打开（快速失败）", circuit_open=True, attempts=0)

        last_error: Optional[Exception] = None
        error_class = ErrorClass.AMBIGUOUS
        attempts = 0
        retry_after: Optional[float] = None

        while True:
            attempts += 1
            try:
                result = fn()
                self.breaker.record_success()
                self._history.append({"ok": True, "attempts": attempts, "ts": time.time()})
                return RetryResult(ok=True, result=result, attempts=attempts)
            except Exception as e:  # noqa: BLE001 —— 重试引擎统一捕获
                last_error = e
                error_class = self.policy.classify(e, classify_hint)
                self.breaker.record_failure()
                retry_after = self._extract_retry_after(e)

                # 持久错误 → 立即失败（重试一万次也一样且烧配额）
                if error_class == ErrorClass.PERSISTENT:
                    break
                # 模糊错误 → 只试一次
                if error_class == ErrorClass.AMBIGUOUS and attempts > 1:
                    break
                # 时间预算耗尽（stop_after_delay）
                if self.policy.budget_exhausted(start):
                    result = RetryResult(ok=False, error=f"时间预算耗尽: {last_error}", attempts=attempts, error_class=error_class.value, exhausted_budget=True)
                    self._history.append(result.to_dict())
                    return result
                # 次数上限
                if attempts > self.policy.max_retries:
                    break
                # 退避等待
                wait = self.policy.delay_for(attempts - 1, retry_after)
                if on_retry is not None:
                    on_retry(attempts, wait, last_error)
                time.sleep(wait)

        result = RetryResult(ok=False, error=str(last_error), attempts=attempts, error_class=error_class.value)
        self._history.append(result.to_dict())
        return result

    @staticmethod
    def _extract_retry_after(e: Exception) -> Optional[float]:
        """从异常中提取 Retry-After 头值。"""
        attrs = getattr(e, "retry_after", None)
        if attrs is not None:
            try:
                return float(attrs)
            except (TypeError, ValueError):
                pass
        # httpx 响应头
        resp = getattr(e, "response", None)
        if resp is not None and hasattr(resp, "headers"):
            val = resp.headers.get("Retry-After")
            if val:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        return None

    def history(self, limit: int = 50) -> list[dict]:
        return self._history[-limit:]
