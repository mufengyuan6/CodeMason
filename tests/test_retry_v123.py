"""G6 智能重试 + 流中断恢复测试（v1.17 落地）。

验收（design.md Phase 1 强制测试）：
- 错误分类（瞬时重试/持久零重试/408 模糊一次）
- 指数退避+full jitter（模拟 thundering herd 不齐撞）
- Retry-After 头尊重
- 时间预算驱动（窗口内成功/窗口到放弃转 fallback）
- 熔断器（连续失败快速失败+周期探测恢复）
- provider 流中断（标中断+不自动拼接；未完成 turn/请求标中断；工具声明幂等才重试）
- JSONL 半行恢复（最后一行残缺 → 从最后完整事件续）
"""

import time

from src.providers.resume import JsonlHalfLineRecovery, RecoveryMode, StreamRecoveryPolicy
from src.providers.retry import CircuitBreaker, ErrorClass, RetryEngine, RetryPolicy


class TestErrorClassification:
    """错误分类学（瞬时重试/持久零重试/408 模糊一次）。"""

    def test_transient_statuses(self):
        policy = RetryPolicy()
        assert policy.classify(Exception("x"), 429) == ErrorClass.TRANSIENT
        assert policy.classify(Exception("x"), 503) == ErrorClass.TRANSIENT
        assert policy.classify(Exception("x"), 500) == ErrorClass.TRANSIENT

    def test_persistent_statuses(self):
        policy = RetryPolicy()
        assert policy.classify(Exception("x"), 400) == ErrorClass.PERSISTENT
        assert policy.classify(Exception("x"), 401) == ErrorClass.PERSISTENT
        assert policy.classify(Exception("x"), 404) == ErrorClass.PERSISTENT

    def test_ambiguous_408(self):
        policy = RetryPolicy()
        assert policy.classify(Exception("x"), 408) == ErrorClass.AMBIGUOUS

    def test_exception_name_inference(self):
        policy = RetryPolicy()
        assert policy.classify(TimeoutError()) == ErrorClass.TRANSIENT
        assert policy.classify(ConnectionResetError()) == ErrorClass.TRANSIENT
        # 持久类：自定义带 auth/forbidden/notfound 名
        class AuthFailedError(Exception):
            pass

        class NotFoundResource(Exception):
            pass

        assert policy.classify(AuthFailedError()) == ErrorClass.PERSISTENT
        assert policy.classify(NotFoundResource()) == ErrorClass.PERSISTENT


class TestRetryEngine:
    """智能重试主流程。"""

    def test_success_first_try(self):
        engine = RetryEngine()
        result = engine.execute(lambda: "ok")
        assert result.ok is True
        assert result.attempts == 1

    def test_persistent_no_retry(self):
        """持久错误 → 立即失败不重试。"""
        calls = []

        def fail():
            calls.append(1)
            raise ValueError("invalid request")

        engine = RetryEngine(policy=RetryPolicy(max_retries=5))
        result = engine.execute(fail, classify_hint=400)
        assert result.ok is False
        assert result.attempts == 1  # 零重试
        assert result.error_class == "persistent"

    def test_transient_retries_then_success(self):
        """瞬时错误 → 重试直到成功。"""
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise TimeoutError("network timeout")
            return "recovered"

        engine = RetryEngine(policy=RetryPolicy(base_delay=0.01, max_delay=0.05))
        result = engine.execute(flaky)
        assert result.ok is True
        assert result.attempts == 3
        assert result.result == "recovered"

    def test_time_budget_exhausted(self):
        """时间预算耗尽 → 放弃（stop_after_delay）。"""
        def always_fail():
            raise TimeoutError("always")

        engine = RetryEngine(policy=RetryPolicy(base_delay=0.05, max_delay=0.1, time_budget=0.15))
        start = time.time()
        result = engine.execute(always_fail)
        assert result.ok is False
        assert result.exhausted_budget is True
        assert (time.time() - start) < 2.0  # 不会无限重试

    def test_retry_after_respected(self):
        """Retry-After 头尊重。"""

        class RetryAfterError(Exception):
            retry_after = 0.01  # 服务端指定等待

        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 2:
                raise RetryAfterError("rate limited")
            return "ok"

        engine = RetryEngine(policy=RetryPolicy(base_delay=0.05, max_delay=0.1))
        result = engine.execute(flaky)
        assert result.ok is True
        assert result.attempts == 2

    def test_max_retries_capped(self):
        calls = []

        def always_fail():
            calls.append(1)
            raise TimeoutError("x")

        engine = RetryEngine(policy=RetryPolicy(base_delay=0.01, max_delay=0.02, max_retries=2))
        result = engine.execute(always_fail)
        assert result.ok is False
        assert result.attempts <= 3  # 初始 + 2 次重试

    def test_on_retry_callback(self):
        """重试回调（退避等待通知）。"""
        retries = []

        def flaky():
            if len(retries) < 1:
                raise TimeoutError("x")
            return "ok"

        engine = RetryEngine(policy=RetryPolicy(base_delay=0.01, max_delay=0.02))
        engine.execute(flaky, on_retry=lambda attempt, wait, err: retries.append(attempt))
        assert retries  # 至少一次重试


class TestCircuitBreaker:
    """熔断器：连续失败快速失败 + 周期探测恢复。"""

    def test_opens_after_threshold(self):
        calls = []

        def fail():
            calls.append(1)
            raise TimeoutError("x")

        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
        engine = RetryEngine(policy=RetryPolicy(base_delay=0.01, max_delay=0.02), breaker=breaker)
        engine.execute(fail)
        assert breaker.is_open is True
        # 熔断打开 → 快速失败（不执行 fn）
        before = len(calls)
        result = engine.execute(fail)
        assert result.circuit_open is True
        assert len(calls) == before  # fn 未被调用

    def test_recovery_after_timeout(self):
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.is_open is True
        time.sleep(0.1)  # 过恢复窗口
        assert breaker.is_open is False  # 周期探测恢复

    def test_success_closes(self):
        breaker = CircuitBreaker(failure_threshold=3)
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        assert breaker.is_open is False


class TestStreamRecovery:
    """provider 流中断策略（G6）。"""

    def test_interrupt_marks_incomplete(self):
        policy = StreamRecoveryPolicy()
        policy.mark_stream_start("s1")
        policy.append_partial("s1", "部分输出")
        result = policy.on_interrupt("s1")
        assert result["interrupted"] is True
        assert result["verification"] == "failed"  # 部分输出不能当完整结果
        assert result["complete"] is False

    def test_no_auto_concat(self):
        """部分流式输出不自动拼接为完整。"""
        policy = StreamRecoveryPolicy()
        policy.mark_stream_start("s1")
        policy.append_partial("s1", "chunk1 ")
        policy.append_partial("s1", "chunk2")
        status = policy._streams["s1"]
        assert status.partial_output == "chunk1 chunk2"  # 只累积，不标记 complete
        assert status.complete is False

    def test_verify_after_interrupt_fails(self):
        policy = StreamRecoveryPolicy()
        policy.mark_stream_start("s1")
        policy.on_interrupt("s1")
        result = policy.verify_complete("s1")
        assert result["verification"] == "failed"

    def test_retry_only_idempotent(self):
        """只有声明幂等才重试（retry_unfinished 模式）。"""
        policy = StreamRecoveryPolicy(recovery_mode=RecoveryMode.RETRY_UNFINISHED)
        assert policy.should_retry("s1", is_idempotent=True) is True
        assert policy.should_retry("s1", is_idempotent=False) is False
        # 默认 mark_interrupted：永不自动重试
        policy2 = StreamRecoveryPolicy()
        assert policy2.should_retry("s1", is_idempotent=True) is False


class TestJsonlHalfLineRecovery:
    """JSONL 半行恢复（G6：最后一行残缺 → 从最后完整事件续）。"""

    def test_strip_half_line(self, tmp_path):
        f = tmp_path / "events.jsonl"
        f.write_text('{"id":1,"type":"a"}\n{"id":2,"type":"b"}\n{"id":3,"ty', encoding="utf-8")  # 最后半行
        info = JsonlHalfLineRecovery.strip_half_line(str(f))
        assert info["truncated"] is True
        assert info["last_valid_line"] == 2
        # 修复后文件只有完整行
        content = f.read_text(encoding="utf-8")
        assert content.count("\n") == 2
        assert "id\":3" not in content  # 半行已丢弃

    def test_clean_file_no_truncation(self, tmp_path):
        f = tmp_path / "ok.jsonl"
        f.write_text('{"id":1}\n{"id":2}\n', encoding="utf-8")
        info = JsonlHalfLineRecovery.strip_half_line(str(f))
        assert info["truncated"] is False
        assert info["last_valid_line"] == 2

    def test_resume_cursor(self, tmp_path):
        f = tmp_path / "events.jsonl"
        f.write_text('{"id":1}\n{"id":2}\n{"id":3,"unfinished', encoding="utf-8")
        cursor = JsonlHalfLineRecovery.resume_cursor(str(f))
        assert cursor == 2  # 从最后完整事件续
