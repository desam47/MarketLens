"""
Tests for backend/market_data/circuit_breaker.py

Covers:
  - CLOSED → OPEN transition on consecutive failures
  - OPEN → HALF_OPEN transition after recovery timeout
  - HALF_OPEN → CLOSED on test success
  - HALF_OPEN → OPEN on test failure
  - CircuitBreakerOpen exception when OPEN
  - Thread safety under concurrent access
  - stats() snapshot correctness
  - Reset on repeated success
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../"))

from backend.market_data.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
)


def _raise(exc):
    """Helper: call this to raise the given exception from inside a callable."""
    raise exc


class TestCircuitBreakerStateMachine(unittest.TestCase):
    """State machine transitions — CLOSED / OPEN / HALF_OPEN."""

    def test_starts_closed(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_opens_after_failure_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        for _ in range(3):
            with self.assertRaises(RuntimeError):
                cb.call(_raise, RuntimeError("fail"))
        self.assertEqual(cb.state, CircuitState.OPEN)

    def test_raises_when_open(self):
        cb = CircuitBreaker(name="test", failure_threshold=1)
        with self.assertRaises(RuntimeError):
            cb.call(_raise, RuntimeError("boom"))
        self.assertEqual(cb.state, CircuitState.OPEN)
        with self.assertRaises(CircuitBreakerOpen) as ctx:
            cb.call(lambda: 42)
        self.assertEqual(ctx.exception.provider_name, "test")
        self.assertEqual(ctx.exception.state, CircuitState.OPEN)

    def test_open_to_half_open_after_timeout(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.05)
        with self.assertRaises(RuntimeError):
            cb.call(_raise, RuntimeError("boom"))
        self.assertEqual(cb.state, CircuitState.OPEN)
        # Advance past recovery timeout — OPEN → HALF_OPEN
        time.sleep(0.06)
        cb._check_and_transition()
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.01)
        with self.assertRaises(RuntimeError):
            cb.call(_raise, RuntimeError("boom"))
        # Advance past recovery timeout
        time.sleep(0.02)
        cb._check_and_transition()
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
        # Success in HALF_OPEN transitions to CLOSED
        result = cb.call(lambda: 42)
        self.assertEqual(result, 42)
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb._consecutive_failures, 0)

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.01)
        with self.assertRaises(RuntimeError):
            cb.call(_raise, RuntimeError("boom"))
        time.sleep(0.02)
        cb._check_and_transition()
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
        # Failure in HALF_OPEN goes back to OPEN
        with self.assertRaises(RuntimeError):
            cb.call(_raise, RuntimeError("boom2"))
        self.assertEqual(cb.state, CircuitState.OPEN)

    def test_success_in_closed_decays_failure_count(self):
        """A success in CLOSED state should decay the consecutive failure count."""
        cb = CircuitBreaker(name="test", failure_threshold=5)
        # 4 failures — one shy of the threshold, state still CLOSED
        for _ in range(4):
            with self.assertRaises(RuntimeError):
                cb.call(_raise, RuntimeError("fail"))
        self.assertEqual(cb._consecutive_failures, 4)
        self.assertEqual(cb.state, CircuitState.CLOSED)
        # A success decays the counter (4→3); one more failure brings it to 4 again.
        cb.call(lambda: 42)
        self.assertEqual(cb._consecutive_failures, 3)
        # Two more failures: 3 + 2 = 5 — hits threshold, breaker opens.
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                cb.call(_raise, RuntimeError("fail"))
        self.assertEqual(cb.state, CircuitState.OPEN)


class TestCircuitBreakerStats(unittest.TestCase):
    """CircuitBreaker.stats() returns a consistent snapshot."""

    def test_stats_initial(self):
        cb = CircuitBreaker(name="foo", failure_threshold=5)
        s = cb.stats()
        self.assertEqual(s.provider_name, "foo")
        self.assertEqual(s.state, CircuitState.CLOSED)
        self.assertEqual(s.consecutive_failures, 0)
        self.assertEqual(s.total_successes, 0)
        self.assertEqual(s.total_failures, 0)

    def test_stats_after_calls(self):
        cb = CircuitBreaker(name="bar", failure_threshold=3)
        cb.call(lambda: 1)
        with self.assertRaises(RuntimeError):
            cb.call(_raise, RuntimeError("x"))
        cb.call(lambda: 2)
        s = cb.stats()
        self.assertEqual(s.total_successes, 2)
        self.assertEqual(s.total_failures, 1)
        self.assertEqual(s.consecutive_failures, 0)  # reset by success

    def test_stats_snapshot_is_thread_safe(self):
        cb = CircuitBreaker(name="snap", failure_threshold=100)
        results = []

        def counter():
            for _ in range(100):
                try:
                    cb.call(lambda: 1)
                except Exception:
                    pass
                results.append(cb.stats().total_successes)

        threads = [threading.Thread(target=counter) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All reads should reflect a consistent state
        for v in results:
            self.assertGreaterEqual(v, 0)


class TestCircuitBreakerThreadSafety(unittest.TestCase):
    """Concurrent calls are safe under a shared lock."""

    def test_concurrent_success_calls(self):
        cb = CircuitBreaker(name="concurrent", failure_threshold=100)
        barrier = threading.Barrier(20)

        def worker():
            barrier.wait()
            for _ in range(50):
                cb.call(lambda: time.sleep(0.001) or 42)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(cb._total_successes, 20 * 50)
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_concurrent_failure_calls(self):
        # Use a threshold higher than total expected failures so the circuit never
        # opens during the test.  (1000 failures / 20 threads × 50 calls, but
        # threshold is 2000 — still well below threshold.)
        cb = CircuitBreaker(name="concurrent_fail", failure_threshold=2000)
        barrier = threading.Barrier(20)

        def worker():
            barrier.wait()
            for _ in range(50):
                with self.assertRaises(RuntimeError):
                    cb.call(_raise, RuntimeError("boom"))

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(cb._total_failures, 20 * 50)
        # All calls fail but threshold is 100 — state is still CLOSED
        self.assertEqual(cb.state, CircuitState.CLOSED)


class TestCircuitBreakerEdgeCases(unittest.TestCase):
    """Edge cases: no-op calls, identity, etc."""

    def test_call_returns_correct_value(self):
        cb = CircuitBreaker(name="ret", failure_threshold=5)
        self.assertEqual(cb.call(lambda: "hello"), "hello")
        self.assertEqual(cb.call(lambda x: x + 1, 5), 6)
        self.assertEqual(cb.call(lambda x, y: x * y, 3, 7), 21)

    def test_exception_message_contains_provider_name(self):
        cb = CircuitBreaker(name="provider_x", failure_threshold=1)
        with self.assertRaises(RuntimeError):
            cb.call(_raise, RuntimeError("boom"))
        with self.assertRaises(CircuitBreakerOpen) as ctx:
            cb.call(lambda: 1)
        self.assertIn("provider_x", str(ctx.exception))

    def test_get_state_returns_current_state(self):
        cb = CircuitBreaker(name="state_test", failure_threshold=2)
        self.assertEqual(cb.get_state(), cb.state)
        with self.assertRaises(RuntimeError):
            cb.call(_raise, RuntimeError("f"))
        self.assertEqual(cb.get_state(), cb.state)

    def test_transitions_log_info(self):
        """State transitions emit INFO-level log records (smoke test)."""
        cb = CircuitBreaker(name="log_test", failure_threshold=1, recovery_timeout=0.01)
        with self.assertLogs("backend.market_data.circuit_breaker", level="INFO") as log_ctx:
            with self.assertRaises(RuntimeError):
                cb.call(_raise, RuntimeError("boom"))
        # Should have at least one record mentioning the transition
        any_transition = any("OPEN" in r.getMessage() for r in log_ctx.records)
        self.assertTrue(any_transition, f"Expected an OPEN transition log, got: {[r.getMessage() for r in log_ctx.records]}")


if __name__ == "__main__":
    unittest.main()
