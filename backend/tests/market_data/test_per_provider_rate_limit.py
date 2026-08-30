"""
Tests for per-provider rate limiting in market_data/services/manager.py.

Covers:
  - _get_per_provider_rate_limit() returns the correct per-provider setting
  - Unknown providers fall back to the global rate_limit_per_minute
  - _PerProviderRateLimiter.acquire() blocks when the limit is exceeded
  - Rate limiting is per-provider (calls to different providers don't interfere)
  - Rate limiter is thread-safe
  - Rate limiter stats() reflect throttled-call counts
  - Rate limiting is skipped when limit is 0 (disabled)
"""
import os
import sys
import threading
import time
import unittest
from collections import deque
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../"))

from backend.market_data.services.manager import (
    _PerProviderRateLimiter,
    _get_per_provider_rate_limit,
)


class TestGetPerProviderRateLimit(unittest.TestCase):
    """_get_per_provider_rate_limit() resolves the correct per-provider limit."""

    def _make_fake_market_data(self, **attrs):
        """Return a plain object that mimics MarketDataSettings.

        A plain object with explicit attributes raises AttributeError on missing
        attributes (the same behavior as a real pydantic BaseSettings instance).
        """
        class FakeMarketData:
            pass

        md = FakeMarketData()
        for k, v in attrs.items():
            setattr(md, k, v)
        return md

    def test_yahoo_finance_uses_dedicated_setting(self):
        """Returns yahoo_finance_rate_limit_per_minute when the field is set."""
        md = self._make_fake_market_data(
            rate_limit_per_minute=30,
            yahoo_finance_rate_limit_per_minute=60,
            webull_rate_limit_per_minute=120,
        )
        with patch("backend.market_data.services.manager._settings") as mock_settings:
            mock_settings.market_data = md
            limit = _get_per_provider_rate_limit("yahoo_finance")
        self.assertEqual(limit, 60)

    def test_webull_uses_dedicated_setting(self):
        md = self._make_fake_market_data(
            rate_limit_per_minute=30,
            yahoo_finance_rate_limit_per_minute=60,
            webull_rate_limit_per_minute=120,
        )
        with patch("backend.market_data.services.manager._settings") as mock_settings:
            mock_settings.market_data = md
            limit = _get_per_provider_rate_limit("webull")
        self.assertEqual(limit, 120)

    def test_unknown_provider_falls_back_to_global(self):
        """For unknown providers, getattr returns the global rate_limit_per_minute."""
        md = self._make_fake_market_data(
            rate_limit_per_minute=30,
            yahoo_finance_rate_limit_per_minute=60,
            webull_rate_limit_per_minute=120,
        )
        with patch("backend.market_data.services.manager._settings") as mock_settings:
            mock_settings.market_data = md
            limit = _get_per_provider_rate_limit("polygon")
        # polygon_rate_limit_per_minute is not set → getattr falls back to 30
        self.assertEqual(limit, 30)

    def test_zero_limit_means_disabled(self):
        """A limit of 0 means rate limiting is disabled for that provider."""
        md = self._make_fake_market_data(rate_limit_per_minute=0)
        with patch("backend.market_data.services.manager._settings") as mock_settings:
            mock_settings.market_data = md
            limit = _get_per_provider_rate_limit("yahoo_finance")
        self.assertEqual(limit, 0)


class TestPerProviderRateLimiter(unittest.TestCase):
    """_PerProviderRateLimiter enforces per-provider call limits."""

    def setUp(self):
        self.limiter = _PerProviderRateLimiter()

    def test_first_call_not_throttled(self):
        """Under the limit, acquire() returns immediately without sleeping."""
        start = time.monotonic()
        self.limiter.acquire("provider_a", max_per_minute=10)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.05)  # should be nearly instant

    def test_allows_exactly_limit_calls(self):
        """Exactly limit calls within the window are allowed."""
        start = time.monotonic()
        for _ in range(5):
            self.limiter.acquire("burst", max_per_minute=5)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.1)

    def test_blocks_over_limit(self):
        """Over the limit, acquire() sleeps until a window slot opens."""
        # Pre-fill the call history with 5 calls at recent timestamps
        now = time.monotonic()
        self.limiter._calls["throttled"] = deque([now - 58, now - 57, now - 55, now - 30, now - 10])

        start = time.monotonic()
        # 6th call should sleep until the oldest call (58s ago) ages out (at ~2s from now)
        self.limiter.acquire("throttled", max_per_minute=5)
        elapsed = time.monotonic() - start
        # Should have slept ~2s (until the 58-second-old call exits the window)
        self.assertGreaterEqual(elapsed, 1.8)
        self.assertLess(elapsed, 5.0)

    def test_rate_limit_is_per_provider(self):
        """Calls to different providers are independent."""
        # Exhaust limit for provider_a
        now = time.monotonic()
        self.limiter._calls["provider_a"] = deque([now - 30, now - 20, now - 10, now - 5, now - 2])

        # provider_b should NOT be affected
        start = time.monotonic()
        self.limiter.acquire("provider_b", max_per_minute=5)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.05)  # provider_b is under limit

    def test_zero_limit_means_disabled(self):
        """max_per_minute=0 skips rate limiting entirely."""
        start = time.monotonic()
        self.limiter.acquire("no_limit", max_per_minute=0)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.01)  # instant, no rate limit check

    def test_stats_tracks_throttled_calls(self):
        """stats() returns the number of times each provider was throttled.

        Pre-populate the limiter so the next call is over the limit, with the
        oldest call only ~1s away from aging out.  This keeps the test fast
        (~1s of sleep) while still exercising the throttle-and-recount path.
        """
        # Inject a fake call timestamp 59 seconds ago. With limit=1, the next
        # call must wait ~1s for that one to age out of the 60s window.
        now = time.monotonic()
        self.limiter._calls["tracked"] = deque([now - 59])
        self.limiter._throttled_count["tracked"] = 0

        start = time.monotonic()
        self.limiter.acquire("tracked", max_per_minute=1)
        elapsed = time.monotonic() - start

        stats = self.limiter.stats()
        self.assertGreaterEqual(stats.get("tracked", 0), 1)
        # Should have slept ~1s waiting for the 59-second-old call to exit the window
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, 5.0)

    def test_window_slides_correctly(self):
        """Calls older than 60 seconds fall out of the sliding window."""
        old_time = time.monotonic() - 120  # 2 minutes ago
        self.limiter._calls["sliding"] = deque([old_time, old_time, old_time])

        start = time.monotonic()
        self.limiter.acquire("sliding", max_per_minute=5)
        elapsed = time.monotonic() - start
        # All 3 calls are outside the 60s window, so 5 new calls fit without blocking
        self.assertLess(elapsed, 0.05)

    def test_multiple_providers_stats(self):
        """Multiple providers each track their own throttle count."""
        self.limiter._throttled_count["provider_x"] = 2
        self.limiter._throttled_count["provider_y"] = 5

        stats = self.limiter.stats()
        self.assertEqual(stats["provider_x"], 2)
        self.assertEqual(stats["provider_y"], 5)


class TestRateLimiterThreadSafety(unittest.TestCase):
    """Rate limiter is safe under concurrent access."""

    def test_concurrent_acquire_no_errors(self):
        """Multiple threads calling acquire() concurrently raises no exceptions."""
        limiter = _PerProviderRateLimiter()
        barrier = threading.Barrier(20)
        errors = []

        def worker(provider_prefix):
            barrier.wait()
            for i in range(10):
                try:
                    limiter.acquire(f"{provider_prefix}_{i % 3}", max_per_minute=1000)
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
