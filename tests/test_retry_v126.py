"""T-26-1 重试状态事件化测试（v1.26，G6）。

验证：RetryPolicy.policy_key() 序列化 + RetryEngine 事件化——
retry/retry-started 事件在正确时机产生（调度先于等待）、policyKey 稳定
（同策略同 key、策略变化 key 变）、尊重 providerRetryAfterMs、计数可重建。
"""

import time

import pytest

from src.protocol import Retry, RetryStarted
from src.providers.retry import (
    CircuitBreaker,
    RetryEngine,
    RetryPolicy,
)


class TestPolicyKey:
    def test_policy_key_stable(self):
        """同策略 → 同 key（计数可累加的前提）。"""
        p1 = RetryPolicy(base_delay=1.0, max_delay=60.0, max_retries=5)
        p2 = RetryPolicy(base_delay=1.0, max_delay=60.0, max_retries=5)
        assert p1.policy_key() == p2.policy_key()

    def test_policy_key_changes_on_params(self):
        """策略参数变化 → key 变化（计数重新开始）。"""
        p1 = RetryPolicy(base_delay=1.0, max_delay=60.0, max_retries=5)
        p2 = RetryPolicy(base_delay=2.0, max_delay=60.0, max_retries=5)
        assert p1.policy_key() != p2.policy_key()

    def test_policy_key_changes_on_time_budget(self):
        """time_budget 变化 → key 变化（时间预算也是策略一部分）。"""
        p1 = RetryPolicy(time_budget=None)
        p2 = RetryPolicy(time_budget=30.0)
        assert p1.policy_key() != p2.policy_key()

    def test_policy_key_deterministic_format(self):
        """key 是确定性 JSON 序列化（可排序、可复现）。"""
        p = RetryPolicy(base_delay=1.0, max_delay=60.0, max_retries=5)
        key = p.policy_key()
        assert isinstance(key, str)
        assert "base_delay" in key or "1.0" in key


class TestRetryEventization:
    def test_retry_event_before_wait(self):
        """Retry 事件先于等待产生（调度决策先落盘）。"""
        events = []
        policy = RetryPolicy(base_delay=0.001, max_delay=0.01, max_retries=2)
        engine = RetryEngine(policy=policy, on_retry_event=events.append)

        def boom():
            raise TimeoutError("connection timeout")

        engine.execute(boom, classify_hint=503)
        assert len(events) >= 1
        assert isinstance(events[0], Retry)
        assert events[0].retry == 1
        assert events[0].policy_key == policy.policy_key()

    def test_retry_started_after_retry(self):
        """RetryStarted 在 Retry 之后产生（等待真正开始前）。"""
        events = []
        policy = RetryPolicy(base_delay=0.001, max_delay=0.01, max_retries=1)
        engine = RetryEngine(policy=policy, on_retry_event=events.append)

        def boom():
            raise TimeoutError("connection timeout")

        engine.execute(boom, classify_hint=503)
        types = [type(e).__name__ for e in events]
        assert "Retry" in types
        assert "RetryStarted" in types
        retry_idx = types.index("Retry")
        started_idx = types.index("RetryStarted")
        assert retry_idx < started_idx  # 调度先于等待
        # 同一 retry_id 配对
        assert events[retry_idx].retry_id == events[started_idx].retry_id

    def test_retry_count_rebuildable_from_events(self):
        """重试计数可从事件流重建（模拟重启后恢复）。"""
        events = []
        policy = RetryPolicy(base_delay=0.001, max_delay=0.01, max_retries=3)
        engine = RetryEngine(policy=policy, on_retry_event=events.append)

        def boom():
            raise TimeoutError("connection timeout")

        engine.execute(boom, classify_hint=503)

        # 模拟重启：仅凭事件流算出已重试次数（不重置为 0）
        retries = [e for e in events if isinstance(e, Retry)]
        rebuilt_count = max(e.retry for e in retries) if retries else 0
        assert rebuilt_count == len(retries) >= 1

    def test_retry_no_event_on_success(self):
        """成功路径不产生重试事件。"""
        events = []
        policy = RetryPolicy(base_delay=0.001, max_delay=0.01, max_retries=3)
        engine = RetryEngine(policy=policy, on_retry_event=events.append)
        result = engine.execute(lambda: "ok")
        assert result.ok
        assert len(events) == 0

    def test_retry_no_event_on_persistent_error(self):
        """持久错误立即失败，不产生重试事件（重试一万次也一样）。"""
        events = []
        policy = RetryPolicy(base_delay=0.001, max_delay=0.01, max_retries=3)
        engine = RetryEngine(policy=policy, on_retry_event=events.append)
        result = engine.execute(lambda: (_ for _ in ()).throw(ValueError("bad request")), classify_hint=400)
        assert not result.ok
        assert result.error_class == "persistent"
        assert len(events) == 0  # 持久错误零重试事件

    def test_retry_after_respected(self):
        """providerRetryAfterMs 优先于本地退避。"""
        events = []
        policy = RetryPolicy(base_delay=1.0, max_delay=5.0, max_retries=1)
        engine = RetryEngine(policy=policy, on_retry_event=events.append)

        class RateLimited(Exception):
            retry_after = 0.001  # 服务端指定等待（秒）

        def boom():
            raise RateLimited()

        engine.execute(boom, classify_hint=429)
        assert len(events) >= 1
        # Retry 事件的 delay_ms 取自服务端 retry_after（0.001s = 1.0ms），
        # 而非本地退避 base×2（1.0s = 1000ms）
        assert events[0].delay_ms == 1.0

    def test_default_no_event_hook(self):
        """不传 on_retry_event → 不破坏现有行为（向后兼容）。"""
        policy = RetryPolicy(base_delay=0.001, max_delay=0.01, max_retries=1)
        engine = RetryEngine(policy=policy)  # 无钩子

        def boom():
            raise TimeoutError("connection timeout")

        result = engine.execute(boom, classify_hint=503)
        assert not result.ok
        assert result.attempts >= 1
