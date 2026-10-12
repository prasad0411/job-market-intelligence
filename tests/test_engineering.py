"""Tests for engineering maturity features: correlation, circuit breaker, retry, hot-reload, contracts."""
import pytest
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCircuitBreaker:
    """Test generalized circuit breaker."""

    def test_starts_closed(self):
        from aggregator.circuit_breaker import CircuitBreaker, State
        cb = CircuitBreaker(name="test", failure_threshold=3)
        assert cb.state == State.CLOSED
        assert cb.allow_request()

    def test_trips_after_threshold(self):
        from aggregator.circuit_breaker import CircuitBreaker, State
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == State.CLOSED
        cb.record_failure()
        assert cb.state == State.OPEN
        assert not cb.allow_request()

    def test_recovery_after_timeout(self):
        from aggregator.circuit_breaker import CircuitBreaker, State
        cb = CircuitBreaker(name="test", failure_threshold=2, reset_timeout=0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == State.OPEN
        time.sleep(0.01)
        assert cb.allow_request()  # transitions to HALF_OPEN
        assert cb.state == State.HALF_OPEN

    def test_closes_after_success_in_half_open(self):
        from aggregator.circuit_breaker import CircuitBreaker, State
        cb = CircuitBreaker(name="test", failure_threshold=2, reset_timeout=0, success_threshold=1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.01)
        cb.allow_request()  # HALF_OPEN
        cb.record_success()
        assert cb.state == State.CLOSED

    def test_registry(self):
        from aggregator.circuit_breaker import CircuitBreakerRegistry
        cb1 = CircuitBreakerRegistry.get("sheets", failure_threshold=5)
        cb2 = CircuitBreakerRegistry.get("sheets")
        assert cb1 is cb2

    def test_stats(self):
        from aggregator.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(name="stats_test")
        stats = cb.stats
        assert stats["name"] == "stats_test"
        assert stats["state"] == "closed"


class TestRetry:
    """Test retry with backoff."""

    def test_succeeds_first_try(self):
        from aggregator.retry import retry
        call_count = 0
        @retry(max_attempts=3, base_delay=0.01)
        def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"
        assert succeed() == "ok"
        assert call_count == 1

    def test_retries_on_failure(self):
        from aggregator.retry import retry
        call_count = 0
        @retry(max_attempts=3, base_delay=0.01)
        def fail_twice():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("transient")
            return "ok"
        assert fail_twice() == "ok"
        assert call_count == 3

    def test_raises_after_max_attempts(self):
        from aggregator.retry import retry
        @retry(max_attempts=2, base_delay=0.01)
        def always_fail():
            raise RuntimeError("permanent")
        with pytest.raises(RuntimeError):
            always_fail()

    def test_backoff_calculation(self):
        from aggregator.retry import RetryPolicy
        policy = RetryPolicy(base_delay=1.0, backoff_factor=2.0, max_delay=10.0, jitter=False)
        assert policy.calculate_delay(0) == 1.0
        assert policy.calculate_delay(1) == 2.0
        assert policy.calculate_delay(2) == 4.0
        assert policy.calculate_delay(10) == 10.0  # capped

    def test_jitter_adds_randomness(self):
        from aggregator.retry import RetryPolicy
        policy = RetryPolicy(base_delay=1.0, backoff_factor=2.0, jitter=True)
        delays = [policy.calculate_delay(2) for _ in range(10)]
        assert len(set(delays)) > 1  # should not all be identical


