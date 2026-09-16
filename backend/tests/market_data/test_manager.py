"""
Tests for MarketDataManager
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../'))

from backend.market_data.services.manager import MarketDataManager
from backend.models.market_data import (
    Bar,
    DataStatus,
    ProviderStatus,
    Quote,
)


class TestMarketDataManager(unittest.TestCase):

    def setUp(self):
        # Clear circuit breakers BEFORE manager init so a clean slate for each test.
        # Breakers are module-level singletons — a breaker opened in one test would
        # otherwise fail-fast in the next test.
        from backend.market_data.services.manager import _circuit_breakers, _cb_lock
        with _cb_lock:
            _circuit_breakers.clear()
        # Make Redis look like a miss-everything cache so tests don't read
        # whatever the live ingestion service has populated.
        self._redis_cache_patcher = patch(
            "backend.market_data.services.manager._redis_cache"
        )
        self.mock_redis = self._redis_cache_patcher.start()
        self.mock_redis.get_quote.return_value = None
        self.mock_redis.get_latest_bar.return_value = None
        self.mock_redis.get_bars.return_value = None
        self.mock_redis.set_quote.return_value = True
        self.mock_redis.set_latest_bar.return_value = True
        self.mock_redis.set_bars.return_value = True
        self.mock_redis.is_available.return_value = True
        self.manager = MarketDataManager()
        # Ensure the bar_repository module is loadable so patch() can find it
        # even though backend.repositories is a namespace package.
        import backend.repositories.bar_repository  # noqa: F401

    def tearDown(self):
        self._redis_cache_patcher.stop()

    def test_initialization(self):
        """Test that manager initializes with Yahoo Finance primary and Finnhub fallback.

        The exact list of registered providers grows as new integrations land
        (alpaca, webull, etc.), so assert on the structural contract instead
        of a hard-coded list: both yfinance and finnhub are present, yfinance
        appears before finnhub in the priority list (yfinance is primary per
        settings), and at least 2 Phase-2 providers are registered.
        """
        self.assertIn("yahoo_finance", self.manager.providers)
        self.assertIn("finnhub", self.manager.providers)
        # yfinance is primary per default settings — must precede finnhub (fallback).
        yf_idx = self.manager.provider_priority.index("yahoo_finance")
        fh_idx = self.manager.provider_priority.index("finnhub")
        self.assertLess(yf_idx, fh_idx, "yfinance must be higher priority than finnhub")
        # At least the two Phase 2 providers present.
        self.assertGreaterEqual(len(self.manager.provider_priority), 2)

    def test_add_provider(self):
        """Test adding a new provider"""
        # Create a mock provider
        mock_provider = MagicMock()
        mock_provider.name = "mock_provider"
        mock_provider.is_available.return_value = True

        initial_count = len(self.manager.providers)
        self.manager.add_provider(mock_provider, priority=0)

        self.assertEqual(len(self.manager.providers), initial_count + 1)
        self.assertIn("mock_provider", self.manager.providers)
        self.assertIn("mock_provider", self.manager.provider_priority)

    def test_remove_provider(self):
        """Test removing a provider"""
        # Add a provider first
        mock_provider = MagicMock()
        mock_provider.name = "mock_provider"
        mock_provider.is_available.return_value = True
        self.manager.add_provider(mock_provider, priority=0)

        self.assertIn("mock_provider", self.manager.providers)

        # Remove it
        self.manager.remove_provider("mock_provider")

        self.assertNotIn("mock_provider", self.manager.providers)
        self.assertNotIn("mock_provider", self.manager.provider_priority)

    def test_get_available_providers(self):
        """Test getting list of available providers"""
        # Replace the real provider with a mock that is available
        mock_provider = MagicMock()
        mock_provider.is_available.return_value = True
        self.manager.providers["yahoo_finance"] = mock_provider
        self.manager.provider_priority = ["yahoo_finance"]

        available = self.manager._get_available_providers()
        self.assertIn("yahoo_finance", available)

    @patch('backend.market_data.services.manager.YFinanceProvider')
    def test_get_quote_success(self, mock_yf_provider_class):
        """Test successful quote retrieval through manager"""
        # Setup mock provider
        mock_provider = MagicMock()
        mock_provider.is_available.return_value = True
        mock_provider.get_quote.return_value = Quote(
            symbol="AAPL",
            price=150.0,
            timestamp=datetime.now(),
            provider="yahoo_finance",
            data_status=DataStatus.DELAYED
        )

        # Replace the provider in manager
        self.manager.providers["yahoo_finance"] = mock_provider
        self.manager.provider_priority = ["yahoo_finance"]

        # Test
        quote = self.manager.get_quote("AAPL")

        # Assertions
        self.assertIsInstance(quote, Quote)
        self.assertEqual(quote.symbol, "AAPL")
        self.assertEqual(quote.price, 150.0)
        mock_provider.get_quote.assert_called_once_with("AAPL")

    @patch('backend.market_data.services.manager.YFinanceProvider')
    def test_get_quote_fallback(self, mock_yf_provider_class):
        """Test fallback when primary provider fails on all retries."""
        # Setup mock providers — primary always raises, fallback succeeds.
        mock_primary = MagicMock()
        mock_primary.name = "yahoo_finance"
        mock_primary.is_available.return_value = True
        mock_primary.get_quote.side_effect = Exception("Primary provider failed")

        mock_fallback = MagicMock()
        mock_fallback.name = "fallback_provider"
        mock_fallback.is_available.return_value = True
        mock_fallback.get_quote.return_value = Quote(
            symbol="AAPL",
            price=149.5,
            timestamp=datetime.now(),
            provider="fallback_provider",
            data_status=DataStatus.DELAYED
        )

        # Replace providers in manager
        self.manager.providers = {
            "yahoo_finance": mock_primary,
            "fallback_provider": mock_fallback
        }
        self.manager.provider_priority = ["yahoo_finance", "fallback_provider"]

        # Test
        quote = self.manager.get_quote("AAPL")

        # Assertions
        self.assertIsInstance(quote, Quote)
        self.assertEqual(quote.symbol, "AAPL")
        self.assertEqual(quote.price, 149.5)  # From fallback provider
        self.assertEqual(quote.provider, "fallback_provider")
        # Primary was retried 2 times before the manager fell through.
        self.assertEqual(mock_primary.get_quote.call_count, 2)
        mock_primary.get_quote.assert_called_with("AAPL")
        mock_fallback.get_quote.assert_called_once_with("AAPL")

    def test_get_provider_statuses(self):
        """Test getting status of all providers"""
        # Mock provider statuses
        with patch.object(self.manager.providers["yahoo_finance"], 'get_provider_status') as mock_status:
            mock_status.return_value = ProviderStatus(
                provider_name="yahoo_finance",
                is_healthy=True,
                timestamp=datetime.now()
            )

            statuses = self.manager.get_provider_statuses()

            self.assertIn("yahoo_finance", statuses)
            self.assertTrue(statuses["yahoo_finance"].is_healthy)
            mock_status.assert_called_once()

    # ------------------------------------------------------------------
    # Phase 2 closure tests — stale data, retry/backoff, cache TTL.
    # ------------------------------------------------------------------

    def _make_bar(self, symbol: str, timeframe: str, ts: datetime) -> Bar:
        return Bar(
            symbol=symbol,
            timestamp=ts,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
            timeframe=timeframe,
            provider="yahoo_finance",
            data_status=DataStatus.LIVE,
        )

    def test_get_historical_bars_stale_returns_stale_status(self):
        """Intraday cache older than TTL is returned with DataStatus.STALE."""
        now = datetime.now()
        old_ts = now - timedelta(seconds=600)  # 10 min old — over TTL (300s)
        # Need 312+ bars to pass the 80% count gate (390 expected for 1m/1d).
        cached = [self._make_bar("AAPL", "1m", old_ts) for _ in range(320)]
        mock_db = MagicMock()

        with patch(
            "backend.repositories.bar_repository"
        ) as mock_repo, \
             patch(
            "backend.market_data.services.manager_class.get_settings"
        ) as mock_get_settings, \
             patch(
            "backend.market_data.services.manager_class.get_redis_cache"
        ) as mock_get_redis_cache:
            mock_repo.get_bars.return_value = cached
            mock_get_redis_cache.return_value = self.mock_redis
            mock_get_settings.return_value.market_data.cache_ttl_seconds = 300
            mock_get_settings.return_value.redis.enabled = True
            self.manager.providers = {
                "yahoo_finance": MagicMock(is_available=lambda: True)
            }
            self.manager.provider_priority = ["yahoo_finance"]

            result = self.manager.get_historical_bars(
                "AAPL", timeframe="1m", range_="1d", db=mock_db
            )

        self.assertEqual(len(result), 320)
        self.assertTrue(all(b.data_status == DataStatus.STALE for b in result))
        # Provider was NOT called — stale cache was returned as-is.
        self.manager.providers["yahoo_finance"].get_historical_bars.assert_not_called()

    def test_get_historical_bars_daily_skips_age_check(self):
        """Daily bars are not flagged stale even if newest is hours old."""
        now = datetime.now()
        old_ts = now - timedelta(hours=24)  # very old, but daily
        cached = [self._make_bar("AAPL", "1d", old_ts) for _ in range(80)]
        mock_db = MagicMock()

        with patch(
            "backend.repositories.bar_repository"
        ) as mock_repo, \
             patch(
            "backend.market_data.services.manager_class.get_settings"
        ) as mock_get_settings, \
             patch(
            "backend.market_data.services.manager_class.get_redis_cache"
        ) as mock_get_redis_cache:
            mock_repo.get_bars.return_value = cached
            mock_get_redis_cache.return_value = self.mock_redis
            mock_get_settings.return_value.market_data.cache_ttl_seconds = 300
            mock_get_settings.return_value.redis.enabled = True
            self.manager.providers = {
                "yahoo_finance": MagicMock(is_available=lambda: True)
            }
            self.manager.provider_priority = ["yahoo_finance"]

            result = self.manager.get_historical_bars(
                "AAPL", timeframe="1d", range_="3mo", db=mock_db
            )

        # Daily bars: NOT tagged stale (age check is skipped for them).
        self.assertTrue(all(b.data_status == DataStatus.LIVE for b in result))

    def test_get_quote_retries_on_transient_failure(self):
        """A transient failure should be retried (2 attempts) before falling through."""
        mock_primary = MagicMock()
        mock_primary.name = "yahoo_finance"
        mock_primary.is_available.return_value = True
        # First attempt fails, second succeeds.
        mock_primary.get_quote.side_effect = [
            Exception("transient 1"),
            Quote(
                symbol="AAPL", price=150.0, timestamp=datetime.now(),
                provider="yahoo_finance", data_status=DataStatus.DELAYED,
            ),
        ]

        self.manager.providers = {"yahoo_finance": mock_primary}
        self.manager.provider_priority = ["yahoo_finance"]

        quote = self.manager.get_quote("AAPL")

        self.assertEqual(quote.price, 150.0)
        # tenacity retried up to 2 times.
        self.assertEqual(mock_primary.get_quote.call_count, 2)

    def test_get_historical_bars_cache_ttl_ceiling(self):
        """When the intraday cache is fresher than TTL, it is used as-is (no provider call)."""
        # Use timezone-aware UTC now to match the production code's
        # ``datetime.now(timezone.utc)`` age calculation — avoids a 4-hour
        # offset when the host's local timezone differs from UTC.
        now = datetime.now(timezone.utc)
        fresh_ts = now - timedelta(seconds=10)  # well under TTL
        cached = [self._make_bar("AAPL", "5m", fresh_ts) for _ in range(80)]
        mock_db = MagicMock()

        # ``manager_class`` calls ``get_settings()`` and ``get_redis_cache()``
        # (functions) rather than the module-level ``_settings`` / ``_redis_cache``
        # attributes, so we patch the functions at their actual call site.
        with patch(
            "backend.repositories.bar_repository"
        ) as mock_repo, \
             patch(
            "backend.market_data.services.manager_class.get_settings"
        ) as mock_get_settings, \
             patch(
            "backend.market_data.services.manager_class.get_redis_cache"
        ) as mock_get_redis_cache:
            mock_repo.get_bars.return_value = cached
            # Mirror the setUp's Redis-miss-everything mock through the getter.
            mock_get_redis_cache.return_value = self.mock_redis
            # Mock settings: TTL = 300s, Redis enabled, defaults for the rest.
            mock_get_settings.return_value.market_data.cache_ttl_seconds = 300
            mock_get_settings.return_value.redis.enabled = True
            self.manager.providers = {
                "yahoo_finance": MagicMock(is_available=lambda: True)
            }
            self.manager.provider_priority = ["yahoo_finance"]

            result = self.manager.get_historical_bars(
                "AAPL", timeframe="5m", range_="1d", db=mock_db
            )

        self.assertEqual(len(result), 80)
        # Not stale, not touched, no provider hit.
        self.assertTrue(all(b.data_status == DataStatus.LIVE for b in result))
        self.manager.providers["yahoo_finance"].get_historical_bars.assert_not_called()


class TestGetCachedProvider(unittest.TestCase):
    """get_cached_provider must construct each provider at most once per
    process and reuse it, never re-run its (possibly network-bound)
    __init__ on every call.

    Regression coverage for a live bug (2026-09-09): get_backfill_primary_
    provider / backfill_service._instantiate_provider used to call
    provider_cls() fresh on every invocation. For WebullProvider, __init__
    does a synchronous network auth handshake — repeating it on every
    gap-fill cycle (which runs on the same asyncio event loop as the live
    1m ingestion tick) blocked that loop long enough to make fresh bars
    land 1-2+ minutes late instead of the intended ~60s.
    """

    def setUp(self):
        from backend.market_data.services import manager as manager_mod
        self.manager_mod = manager_mod
        manager_mod._clear_provider_cache()

    def tearDown(self):
        self.manager_mod._clear_provider_cache()

    def test_second_call_reuses_the_same_instance(self):
        construct_count = {"n": 0}

        class _FakeProvider:
            def __init__(self):
                construct_count["n"] += 1

        with patch.object(
            self.manager_mod, "_PROVIDER_CLASSES", {"fake": _FakeProvider}
        ):
            first = self.manager_mod.get_cached_provider("fake")
            second = self.manager_mod.get_cached_provider("fake")

        self.assertIs(first, second)
        self.assertEqual(construct_count["n"], 1)

    def test_adopts_market_data_manager_instance_instead_of_constructing_again(self):
        """Regression: live 2026-09-16, MarketDataManager._initialize_providers()
        (a separate startup code path) and this cache's first caller raced
        to each build their own WebullProvider within the same second. If
        MarketDataManager already has a live instance for `name`, reuse it
        instead of running a second (network-bound) construction."""
        construct_count = {"n": 0}

        class _FakeProvider:
            def __init__(self):
                construct_count["n"] += 1

        already_built = _FakeProvider()
        construct_count["n"] = 0  # only count constructions *through the cache*

        with patch.object(
            self.manager_mod, "_PROVIDER_CLASSES", {"fake": _FakeProvider}
        ), patch.object(
            self.manager_mod.market_data_manager, "providers", {"fake": already_built}
        ):
            result = self.manager_mod.get_cached_provider("fake")

        self.assertIs(result, already_built)
        self.assertEqual(construct_count["n"], 0)

    def test_unknown_provider_returns_none(self):
        with patch.object(self.manager_mod, "_PROVIDER_CLASSES", {}):
            self.assertIsNone(self.manager_mod.get_cached_provider("bogus"))

    def test_construction_failure_is_not_cached_and_retries(self):
        """A transient failure (e.g. a rate-limited auth call) must not
        be remembered as 'permanently unavailable' — the next call gets
        a fresh construction attempt."""
        attempt = {"n": 0}

        class _FlakyProvider:
            def __init__(self):
                attempt["n"] += 1
                if attempt["n"] == 1:
                    raise RuntimeError("transient failure")

        with patch.object(
            self.manager_mod, "_PROVIDER_CLASSES", {"flaky": _FlakyProvider}
        ):
            first = self.manager_mod.get_cached_provider("flaky")
            self.assertIsNone(first)
            second = self.manager_mod.get_cached_provider("flaky")

        self.assertIsNotNone(second)
        self.assertEqual(attempt["n"], 2)

    def test_get_backfill_primary_provider_uses_the_cache(self):
        construct_count = {"n": 0}

        class _FakeProvider:
            def __init__(self):
                construct_count["n"] += 1

        with patch.object(
            self.manager_mod, "_PROVIDER_CLASSES", {"fake": _FakeProvider}
        ), patch(
            "backend.config.settings.BackfillSettings.get_primary_provider",
            return_value="fake",
        ):
            first = self.manager_mod.get_backfill_primary_provider("1m")
            second = self.manager_mod.get_backfill_primary_provider("1m")

        self.assertIs(first, second)
        self.assertEqual(construct_count["n"], 1)

    def test_backfill_service_instantiate_provider_uses_the_cache(self):
        from backend.market_data.services import backfill_service

        construct_count = {"n": 0}

        class _FakeProvider:
            def __init__(self):
                construct_count["n"] += 1

        with patch.object(
            self.manager_mod, "_PROVIDER_CLASSES", {"fake": _FakeProvider}
        ):
            first = backfill_service._instantiate_provider("fake")
            second = backfill_service._instantiate_provider("fake")

        self.assertIs(first, second)
        self.assertEqual(construct_count["n"], 1)

    def test_concurrent_callers_construct_only_once(self):
        """Regression for a live bug (2026-09-16): warmup_tape_engines()
        spawns one thread per watchlist symbol, each calling
        get_cached_provider("webull"). With no lock, every thread sees a
        cold cache at the same instant and runs its own (network-bound)
        __init__ — for a ~17-symbol watchlist that fired ~17 concurrent
        Webull auth handshakes and tripped Webull's own rate limiter
        (429 TOO_MANY_REQUESTS). Racing callers must share one
        construction instead."""
        import threading
        import time

        construct_count = {"n": 0}
        count_lock = threading.Lock()
        n_threads = 8
        start_barrier = threading.Barrier(n_threads)

        class _SlowProvider:
            def __init__(self):
                with count_lock:
                    construct_count["n"] += 1
                # Widen the race window the way a real network-bound
                # __init__ (e.g. WebullProvider's auth handshake) would.
                time.sleep(0.05)

        results: list = []
        results_lock = threading.Lock()

        def _call():
            start_barrier.wait(timeout=2)  # every thread reaches the call together
            instance = self.manager_mod.get_cached_provider("slow")
            with results_lock:
                results.append(instance)

        with patch.object(
            self.manager_mod, "_PROVIDER_CLASSES", {"slow": _SlowProvider}
        ):
            threads = [threading.Thread(target=_call) for _ in range(n_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        self.assertEqual(construct_count["n"], 1)
        self.assertEqual(len(results), n_threads)
        self.assertTrue(all(r is results[0] for r in results))


if __name__ == '__main__':
    unittest.main()
