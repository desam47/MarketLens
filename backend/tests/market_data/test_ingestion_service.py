"""
Tests for MarketDataIngestionService.

Covers:
  - start/stop lifecycle
  - correlation ID propagation from request context into the daemon thread
"""
import asyncio
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../"))

from backend.observability.logging_enhanced import get_correlation_id, set_correlation_id


class TestIngestionServiceLifecycle(unittest.TestCase):
    """Basic start/stop without requiring a live market data provider."""

    def setUp(self):
        from backend.market_data.services.ingestion_service import MarketDataIngestionService

        self.service = MarketDataIngestionService(symbols=["AAPL"], timeframes=["1m"])

    def tearDown(self):
        if self.service.is_running:
            self.service.stop()
            time.sleep(0.2)  # Let the thread drain

    def test_start_sets_is_running(self):
        self.service.start()
        time.sleep(0.1)
        self.assertTrue(self.service.is_running)
        self.service.stop()
        time.sleep(0.2)
        self.assertFalse(self.service.is_running)

    def test_double_start_is_noop(self):
        self.service.start()
        time.sleep(0.05)
        first_thread = self.service._thread
        self.service.start()  # idempotent
        time.sleep(0.05)
        self.assertIs(self.service._thread, first_thread)
        self.service.stop()
        time.sleep(0.2)


class TestCorrelationIdPropagation(unittest.TestCase):
    """Correlation IDs set in the request thread must reach the daemon thread.

    When start() is called from a context with a correlation ID in the
    contextvar, the daemon thread's asyncio loop must see that same ID
    restored at the top of its event loop, so ingestion loop log lines
    are traceable back to the request that initiated ingestion.
    """

    def setUp(self):
        from backend.market_data.services.ingestion_service import MarketDataIngestionService

        self.service = MarketDataIngestionService(symbols=["AAPL"], timeframes=["1m"])
        self.seen_ids: list[str | None] = []
        self._lock = threading.Lock()

    def tearDown(self):
        if self.service.is_running:
            self.service.stop()
            time.sleep(0.3)
        set_correlation_id(None)

    def _check_id_from_thread(self) -> str | None:
        """Called from inside the daemon thread — captures the correlation ID."""
        return get_correlation_id()

    def test_daemon_thread_receives_correlation_id(self):
        """The daemon thread's event loop must re-install the captured
        correlation ID at startup, so background ingestion logs carry the
        request's correlation ID."""
        corr_id = "test-correlation-abc123"
        set_correlation_id(corr_id)

        # Wrap _run_loops so it captures the correlation ID as seen from
        # inside the daemon thread, but otherwise exits quickly.
        orig_run_loops = self.service._run_loops

        async def patched_run_loops():
            # Snapshot the contextvar from inside the daemon thread's
            # event loop. We must check BEFORE creating any sub-tasks,
            # because the restoration happens in start()'s _run_loop wrapper.
            seen = get_correlation_id()
            with self._lock:
                self.seen_ids.append(seen)
            # Yield once so the loop is actually entered, then return so
            # run_until_complete() finishes and stop() can join the thread.
            await asyncio.sleep(0.05)

        self.service._run_loops = patched_run_loops

        try:
            self.service.start()
            # Give the thread time to enter the loop and snapshot the ID.
            for _ in range(20):
                time.sleep(0.05)
                if self.seen_ids:
                    break

            self.assertEqual(
                len(self.seen_ids), 1,
                "Daemon thread should have recorded exactly one ID snapshot",
            )
            self.assertEqual(
                self.seen_ids[0], corr_id,
                f"Daemon thread should see the request's correlation ID "
                f"(expected {corr_id!r}, got {self.seen_ids[0]!r})",
            )
        finally:
            self.service._run_loops = orig_run_loops
            self.service.stop()
            time.sleep(0.3)

    def test_no_correlation_id_does_not_crash(self):
        """start() must not crash if no correlation ID has been set."""
        set_correlation_id(None)

        orig_run_loops = self.service._run_loops

        async def quick_exit():
            await asyncio.sleep(0.05)

        self.service._run_loops = quick_exit

        try:
            self.service.start()
            # The thread should have started even without a correlation ID.
            self.assertIsNotNone(self.service._thread)
            self.assertTrue(self.service._thread.is_alive())
            # Wait for it to exit cleanly.
            self.service._thread.join(timeout=2.0)
            self.assertFalse(
                self.service._thread.is_alive(),
                "Thread should exit cleanly when _run_loops returns",
            )
        finally:
            self.service._run_loops = orig_run_loops
            if self.service.is_running:
                self.service.stop()
                time.sleep(0.3)


if __name__ == "__main__":
    unittest.main()
