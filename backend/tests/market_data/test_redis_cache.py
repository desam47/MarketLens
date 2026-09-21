"""
Tests for RedisCache class in backend/market_data/services/manager.py

Tests the Redis-based caching layer with focus on:
- Hit/miss behavior when Redis is available
- Fallback to None when Redis is unavailable
- Pub/sub for real-time updates
- Proper error handling
"""

import os
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../"))

from backend.market_data.services.cache import (
    _bar_to_json,
    _bars_to_json,
    _quote_to_json,
    get_bar_cache_ttl,
)
from backend.market_data.services.manager import RedisCache
from backend.models.market_data import Bar, DataStatus, Quote


class TestRedisCacheInitialization(unittest.TestCase):
    """Test RedisCache initialization behavior."""

    @patch("backend.market_data.services.manager._settings")
    def test_disabled_redis_no_connection(self, mock_settings):
        """When Redis is disabled, no connection is attempted."""
        mock_settings.redis.enabled = False
        cache = RedisCache()
        self.assertIsNone(cache._client)
        self.assertIsNone(cache._pubsub)
        self.assertFalse(cache.is_available())

    @patch("backend.market_data.services.manager._settings")
    @patch("backend.market_data.services.manager.redis.Redis")
    def test_enabled_redis_connects(self, mock_redis_class, mock_settings):
        """When Redis is enabled, connection is attempted."""
        mock_settings.redis.enabled = True
        mock_settings.redis.url = "redis://localhost:6379"
        mock_settings.redis.password = None

        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_redis_class.from_url.return_value = mock_client

        cache = RedisCache()
        self.assertTrue(cache.is_available())

    @patch("backend.market_data.services.manager._settings")
    @patch("backend.market_data.services.manager.redis.Redis")
    def test_redis_connection_failure_falls_back(self, mock_redis_class, mock_settings):
        """When Redis connection fails, falls back to no-cache state."""
        mock_settings.redis.enabled = True
        mock_settings.redis.url = "redis://localhost:6379"
        mock_settings.redis.password = None

        mock_redis_class.from_url.side_effect = ConnectionError("Cannot connect")

        cache = RedisCache()
        # Falls back to no-cache gracefully
        self.assertFalse(cache.is_available())


class TestRedisCacheGetSet(unittest.TestCase):
    """Test get/set behavior."""

    def setUp(self):
        # Set up a RedisCache with a mocked client
        self.mock_client = MagicMock()
        self.mock_client.ping.return_value = True

        with (
            patch("backend.market_data.services.manager._settings") as mock_settings,
            patch("backend.market_data.services.manager.redis.Redis") as mock_redis_class,
        ):
            mock_settings.redis.enabled = True
            mock_settings.redis.url = "redis://localhost:6379"
            mock_settings.redis.password = None
            mock_settings.redis.bar_data_ttl = 300
            mock_settings.redis.quote_ttl = 60
            mock_redis_class.from_url.return_value = self.mock_client
            self.cache = RedisCache()

    def test_get_bars_hit(self):
        """Should return cached bars when key exists."""
        bar_dict = {
            "symbol": "AAPL",
            "timestamp": "2026-01-01T10:00:00",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1000,
            "timeframe": "1d",
            "provider": "yahoo_finance",
            "data_status": "LIVE",
        }
        import json

        self.mock_client.get.return_value = json.dumps([bar_dict])

        result = self.cache.get_bars("AAPL", "1d")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].symbol, "AAPL")

    def test_get_bars_miss(self):
        """Should return None when key doesn't exist."""
        self.mock_client.get.return_value = None
        result = self.cache.get_bars("AAPL", "1d")
        self.assertIsNone(result)

    def test_get_bars_unavailable_redis(self):
        """Should return None when Redis is unavailable."""
        with (
            patch("backend.market_data.services.manager._settings") as mock_settings,
            patch("backend.market_data.services.manager.redis.Redis"),
        ):
            mock_settings.redis.enabled = False
            cache = RedisCache()
            result = cache.get_bars("AAPL", "1d")
            self.assertIsNone(result)

    def test_set_bars_success(self):
        """Should cache bars with TTL."""
        bar = Bar(
            symbol="AAPL",
            timestamp=datetime.now(),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
            timeframe="1d",
            provider="yahoo_finance",
            data_status=DataStatus.LIVE,
        )

        result = self.cache.set_bars("AAPL", "1d", [bar])
        self.assertTrue(result)
        self.mock_client.setex.assert_called()

    def test_set_bars_unavailable_redis(self):
        """Should return False when Redis is unavailable."""
        with (
            patch("backend.market_data.services.manager._settings") as mock_settings,
            patch("backend.market_data.services.manager.redis.Redis"),
        ):
            mock_settings.redis.enabled = False
            cache = RedisCache()
            result = cache.set_bars("AAPL", "1d", [])
            self.assertFalse(result)

    def test_get_quote_hit(self):
        """Should return cached quote when key exists."""
        quote_dict = {
            "symbol": "AAPL",
            "timestamp": "2026-01-01T10:00:00",
            "price": 150.0,
            "provider": "yahoo_finance",
            "data_status": "LIVE",
            "volume": 1000,
        }
        import json

        self.mock_client.get.return_value = json.dumps(quote_dict)

        result = self.cache.get_quote("AAPL")
        self.assertIsNotNone(result)
        self.assertEqual(result.symbol, "AAPL")
        self.assertEqual(result.price, 150.0)

    def test_get_quote_miss(self):
        """Should return None when key doesn't exist."""
        self.mock_client.get.return_value = None
        result = self.cache.get_quote("AAPL")
        self.assertIsNone(result)

    def test_get_latest_bar_hit(self):
        """Should return cached latest bar when key exists."""
        import json

        bar_dict = {
            "symbol": "AAPL",
            "timestamp": "2026-01-01T10:00:00",
            "open": 100.0,
            "high": 105.0,
            "low": 98.0,
            "close": 103.0,
            "volume": 1000000,
            "timeframe": "1d",
            "provider": "yahoo_finance",
            "data_status": "LIVE",
        }
        self.mock_client.get.return_value = json.dumps(bar_dict)

        result = self.cache.get_latest_bar("AAPL", "1d")
        self.assertIsNotNone(result)
        self.assertEqual(result.symbol, "AAPL")
        self.assertEqual(result.close, 103.0)
        self.mock_client.get.assert_called_with("marketlens:latest_bar:AAPL:1d")

    def test_get_latest_bar_miss(self):
        """Should return None when key doesn't exist."""
        self.mock_client.get.return_value = None
        result = self.cache.get_latest_bar("AAPL", "1d")
        self.assertIsNone(result)

    def test_set_latest_bar(self):
        """Should cache the latest bar and publish an update."""
        bar = Bar(
            symbol="AAPL",
            timestamp=datetime.now(),
            open=100.0,
            high=105.0,
            low=98.0,
            close=103.0,
            volume=1000000,
            timeframe="1d",
            provider="yahoo_finance",
            data_status=DataStatus.LIVE,
        )
        with (
            patch("backend.market_data.services.manager._settings") as mock_settings,
            patch("backend.market_data.services.manager.redis.Redis") as mock_redis_class,
        ):
            mock_settings.redis.enabled = True
            mock_settings.redis.url = "redis://localhost:6379"
            mock_settings.redis.password = None
            mock_settings.redis.bar_data_ttl = 300
            mock_redis_class.from_url.return_value = self.mock_client

            cache = RedisCache()
            result = cache.set_latest_bar("AAPL", "1d", bar)

            self.assertTrue(result)
            self.mock_client.setex.assert_called_once()
            call_args = self.mock_client.setex.call_args
            self.assertEqual(call_args[0][0], "marketlens:latest_bar:AAPL:1d")
            # Per-TF TTL from get_bar_cache_ttl("1d") = 600s
            self.assertEqual(call_args[0][1], 600)
            self.mock_client.publish.assert_called_once()
            pub_call = self.mock_client.publish.call_args[0]
            self.assertEqual(pub_call[0], "marketlens:bar_updates:AAPL:1d")

    def test_set_latest_bar_unavailable_redis(self):
        """Should return False when Redis is unavailable."""
        with (
            patch("backend.market_data.services.manager._settings") as mock_settings,
            patch("backend.market_data.services.manager.redis.Redis"),
        ):
            mock_settings.redis.enabled = False
            cache = RedisCache()
            result = cache.set_latest_bar(
                "AAPL",
                "1d",
                Bar(
                    symbol="AAPL",
                    timestamp=datetime.now(),
                    open=100.0,
                    high=105.0,
                    low=98.0,
                    close=103.0,
                    volume=1000000,
                    timeframe="1d",
                    provider="yahoo_finance",
                    data_status=DataStatus.LIVE,
                ),
            )
            self.assertFalse(result)


class TestBarCacheTTL(unittest.TestCase):
    """Phase 3.1: per-TF cache TTLs for resampled bar series.

    Higher-TF resamples (1d, 1wk) are stable for longer than 1m, so we
    cache them for longer. ``set_bars`` must honour the override when
    supplied, fall back to the per-TF default otherwise, and only fall
    back to the global ``bar_data_ttl`` when the TF is unknown.
    """

    def test_get_bar_cache_ttl_known_timeframes(self):
        """Known TFs return their per-TF TTL."""
        self.assertEqual(get_bar_cache_ttl("1m"), 60)
        self.assertEqual(get_bar_cache_ttl("5m"), 120)
        self.assertEqual(get_bar_cache_ttl("15m"), 180)
        self.assertEqual(get_bar_cache_ttl("30m"), 240)
        self.assertEqual(get_bar_cache_ttl("1h"), 300)
        self.assertEqual(get_bar_cache_ttl("4h"), 360)
        self.assertEqual(get_bar_cache_ttl("1d"), 600)
        self.assertEqual(get_bar_cache_ttl("1wk"), 3600)
        self.assertEqual(get_bar_cache_ttl("1mo"), 3600)

    def test_get_bar_cache_ttl_unknown_returns_default(self):
        """Unknown TFs return the default 300s."""
        self.assertEqual(get_bar_cache_ttl("2m"), 300)
        self.assertEqual(get_bar_cache_ttl("2h"), 300)

    def test_set_bars_uses_default_bar_data_ttl(self):
        """When no TTL override is passed, the global bar_data_ttl is used."""
        bar = Bar(
            symbol="AAPL",
            timestamp=datetime.now(),
            open=100,
            high=101,
            low=99,
            close=100.5,
            volume=1000,
            timeframe="1d",
            provider="yahoo_finance",
            data_status=DataStatus.LIVE,
        )
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        with (
            patch("backend.market_data.services.manager._settings") as mock_settings,
            patch("backend.market_data.services.manager.redis.Redis") as mock_redis_class,
        ):
            mock_settings.redis.enabled = True
            mock_settings.redis.url = "redis://localhost:6379"
            mock_settings.redis.password = None
            mock_settings.redis.bar_data_ttl = 300
            mock_redis_class.from_url.return_value = mock_client
            cache = RedisCache()

            cache.set_bars("AAPL", "1d", [bar])

        call_args = mock_client.setex.call_args
        self.assertEqual(call_args[0][1], 300)

    def test_set_bars_uses_ttl_override(self):
        """An explicit ttl= argument is honoured regardless of the TF."""
        bar = Bar(
            symbol="AAPL",
            timestamp=datetime.now(),
            open=100,
            high=101,
            low=99,
            close=100.5,
            volume=1000,
            timeframe="1d",
            provider="yahoo_finance",
            data_status=DataStatus.LIVE,
        )
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        with (
            patch("backend.market_data.services.manager._settings") as mock_settings,
            patch("backend.market_data.services.manager.redis.Redis") as mock_redis_class,
        ):
            mock_settings.redis.enabled = True
            mock_settings.redis.url = "redis://localhost:6379"
            mock_settings.redis.password = None
            mock_settings.redis.bar_data_ttl = 300
            mock_redis_class.from_url.return_value = mock_client
            cache = RedisCache()

            cache.set_bars("AAPL", "1d", [bar], ttl=42)

        call_args = mock_client.setex.call_args
        self.assertEqual(call_args[0][1], 42)

    def test_set_bars_per_tf_ttl_via_getter(self):
        """Wiring pattern: callers pass ttl=get_bar_cache_ttl(tf) explicitly."""
        bar = Bar(
            symbol="AAPL",
            timestamp=datetime.now(),
            open=100,
            high=101,
            low=99,
            close=100.5,
            volume=1000,
            timeframe="1d",
            provider="yahoo_finance",
            data_status=DataStatus.LIVE,
        )
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        with (
            patch("backend.market_data.services.manager._settings") as mock_settings,
            patch("backend.market_data.services.manager.redis.Redis") as mock_redis_class,
        ):
            mock_settings.redis.enabled = True
            mock_settings.redis.url = "redis://localhost:6379"
            mock_settings.redis.password = None
            # Even if the global default is 300, the explicit per-TF
            # TTL for 1d (600) takes precedence.
            mock_settings.redis.bar_data_ttl = 300
            mock_redis_class.from_url.return_value = mock_client
            cache = RedisCache()

            cache.set_bars("AAPL", "1d", [bar], ttl=get_bar_cache_ttl("1d"))

        call_args = mock_client.setex.call_args
        self.assertEqual(call_args[0][1], 600)


class TestRedisCachePubSub(unittest.TestCase):
    """Test pub/sub functionality."""

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_client.ping.return_value = True
        self.mock_pubsub = MagicMock()
        self.mock_client.pubsub.return_value = self.mock_pubsub

        with (
            patch("backend.market_data.services.manager._settings") as mock_settings,
            patch("backend.market_data.services.manager.redis.Redis") as mock_redis_class,
        ):
            mock_settings.redis.enabled = True
            mock_settings.redis.url = "redis://localhost:6379"
            mock_settings.redis.password = None
            mock_redis_class.from_url.return_value = self.mock_client
            self.cache = RedisCache()

    def test_publish_bar_update(self):
        """Should publish bar update to Redis channel."""
        bar = Bar(
            symbol="AAPL",
            timestamp=datetime.now(),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
            timeframe="1d",
            provider="yahoo_finance",
            data_status=DataStatus.LIVE,
        )
        self.cache.publish_bar_update("AAPL", "1d", bar)
        self.mock_client.publish.assert_called_once()

    def test_publish_quote_update(self):
        """Should publish quote update to Redis channel."""
        quote = Quote(
            symbol="AAPL",
            timestamp=datetime.now(),
            price=150.0,
            provider="yahoo_finance",
            data_status=DataStatus.LIVE,
        )
        self.cache.publish_quote_update("AAPL", quote)
        self.mock_client.publish.assert_called_once()

    def test_subscribe_to_bar_updates(self):
        """Should subscribe to bar updates channel."""
        callback = MagicMock()
        self.cache.subscribe_to_bar_updates("AAPL", "1d", callback)
        self.mock_pubsub.subscribe.assert_called_once()

    def test_subscribe_to_quote_updates(self):
        """Should subscribe to quote updates channel."""
        callback = MagicMock()
        self.cache.subscribe_to_quote_updates("AAPL", callback)
        self.mock_pubsub.subscribe.assert_called_once()

    def test_publish_when_redis_unavailable(self):
        """Should silently skip publishing when Redis is unavailable."""
        with (
            patch("backend.market_data.services.manager._settings") as mock_settings,
            patch("backend.market_data.services.manager.redis.Redis"),
        ):
            mock_settings.redis.enabled = False
            cache = RedisCache()

            quote = Quote(
                symbol="AAPL",
                timestamp=datetime.now(),
                price=150.0,
                provider="yahoo_finance",
                data_status=DataStatus.LIVE,
            )
            # Should not raise
            cache.publish_quote_update("AAPL", quote)


class TestRedisCacheReconnectBehavior(unittest.TestCase):
    """Test that the cache degrades gracefully when Redis goes away mid-session.

    The pub/sub system (publish/subscribe) has no built-in reconnect loop,
    so the correct behaviour is: publish calls become no-ops, is_available()
    starts returning False, and no exceptions propagate up.  This mirrors how
    the real application behaves in a Redis-failover scenario.
    """

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_client.ping.return_value = True
        self.mock_client.pubsub.return_value = MagicMock()

        with (
            patch("backend.market_data.services.manager._settings") as mock_settings,
            patch("backend.market_data.services.manager.redis.Redis") as mock_redis_class,
        ):
            mock_settings.redis.enabled = True
            mock_settings.redis.url = "redis://localhost:6379"
            mock_settings.redis.password = None
            mock_redis_class.from_url.return_value = self.mock_client
            self.cache = RedisCache()

    def test_publish_bar_update_becomes_noop_when_redis_goes_down(self):
        """When Redis disconnects mid-session, publish_bar_update must not raise."""
        # First verify it works.
        bar = Bar(
            symbol="AAPL",
            timestamp=datetime.now(),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
            timeframe="1d",
            provider="yahoo_finance",
            data_status=DataStatus.LIVE,
        )
        self.cache.publish_bar_update("AAPL", "1d", bar)
        self.mock_client.publish.assert_called()

        # Simulate Redis going down: ping starts raising.
        self.mock_client.ping.side_effect = ConnectionError("disconnected")

        # Must not raise.
        self.cache.publish_bar_update("AAPL", "1d", bar)
        # publish should NOT have been called again (early return in publish_bar_update).
        self.mock_client.publish.assert_called_once()

    def test_publish_quote_update_becomes_noop_when_redis_goes_down(self):
        """When Redis disconnects mid-session, publish_quote_update must not raise."""
        quote = Quote(
            symbol="AAPL",
            timestamp=datetime.now(),
            price=150.0,
            provider="yahoo_finance",
            data_status=DataStatus.LIVE,
        )
        self.cache.publish_quote_update("AAPL", quote)
        self.mock_client.publish.assert_called()

        self.mock_client.ping.side_effect = ConnectionError("disconnected")

        # Must not raise.
        self.cache.publish_quote_update("AAPL", quote)
        self.mock_client.publish.assert_called_once()  # no second call

    def test_is_available_returns_false_after_redis_goes_down(self):
        """is_available() must reflect the current connection state."""
        self.assertTrue(self.cache.is_available())

        self.mock_client.ping.side_effect = ConnectionError("disconnected")

        self.assertFalse(self.cache.is_available())

    def test_subscribe_is_noop_when_redis_unavailable(self):
        """subscribe_to_* calls must not raise when Redis is down."""
        callback = MagicMock()

        self.mock_client.ping.side_effect = ConnectionError("disconnected")

        # Must not raise.
        self.cache.subscribe_to_bar_updates("AAPL", "1d", callback)
        self.cache.subscribe_to_quote_updates("AAPL", callback)


class TestRedisCacheErrorHandling(unittest.TestCase):
    """Test error handling and graceful degradation."""

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_client.ping.return_value = True
        self.mock_client.pubsub.return_value = MagicMock()

        with (
            patch("backend.market_data.services.manager._settings") as mock_settings,
            patch("backend.market_data.services.manager.redis.Redis") as mock_redis_class,
        ):
            mock_settings.redis.enabled = True
            mock_settings.redis.url = "redis://localhost:6379"
            mock_settings.redis.password = None
            mock_redis_class.from_url.return_value = self.mock_client
            self.cache = RedisCache()

    def test_get_bars_handles_json_error(self):
        """Should return None when JSON parsing fails."""
        self.mock_client.get.return_value = "invalid json{{"
        result = self.cache.get_bars("AAPL", "1d")
        self.assertIsNone(result)

    def test_get_bars_handles_redis_error(self):
        """Should return None when Redis get fails."""
        self.mock_client.get.side_effect = ConnectionError("Redis disconnected")
        result = self.cache.get_bars("AAPL", "1d")
        self.assertIsNone(result)

    def test_set_bars_handles_redis_error(self):
        """Should return False when Redis setex fails."""
        self.mock_client.setex.side_effect = ConnectionError("Redis disconnected")
        result = self.cache.set_bars("AAPL", "1d", [])
        self.assertFalse(result)

    def test_get_quote_handles_redis_error(self):
        """Should return None when Redis fails."""
        self.mock_client.get.side_effect = ConnectionError("Redis disconnected")
        result = self.cache.get_quote("AAPL")
        self.assertIsNone(result)

    def test_set_quote_handles_redis_error(self):
        """Should return False when Redis fails."""
        self.mock_client.setex.side_effect = ConnectionError("Redis disconnected")
        quote = Quote(
            symbol="AAPL",
            timestamp=datetime.now(),
            price=150.0,
            provider="yahoo_finance",
            data_status=DataStatus.LIVE,
        )
        result = self.cache.set_quote("AAPL", quote)
        self.assertFalse(result)


class TestInvalidateBarsForSymbol(unittest.TestCase):
    """Phase 3.1 cache-invalidation hook: after a 1m bar is upserted, the
    ingestion service must invalidate all cached bar series for that symbol
    so the next read recomputes from the DB instead of serving the stale
    cached version (TTL can be 1h for 1wk)."""

    def _make_bar(self, symbol: str, timeframe: str = "1m"):
        from datetime import datetime

        return Bar(
            symbol=symbol,
            timeframe=timeframe,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
            timestamp=datetime.now(),
            provider="test",
            data_status=DataStatus.HISTORICAL,
        )

    def test_invalidate_deletes_bar_series_keys(self):
        """Both bar series and latest_bar keys for the symbol are deleted."""
        cache = RedisCache()
        cache._client = MagicMock()
        # First pattern: bar series. Second pattern: latest_bar.
        # Each pattern's SCAN loop runs once (cursor=0) and returns its batch.
        cache._client.scan.side_effect = [
            (0, ["marketlens:bars:AAPL:1d", "marketlens:bars:AAPL:1h"]),
            (0, ["marketlens:latest_bar:AAPL:1m"]),
        ]

        n = cache.invalidate_bars_for_symbol("AAPL")
        self.assertEqual(n, 3)
        # Two DELETE calls — one per pattern, each batched.
        self.assertEqual(cache._client.delete.call_count, 2)

    def test_invalidate_does_not_touch_other_symbols(self):
        """Only the named symbol's keys are scanned — others are untouched."""
        cache = RedisCache()
        cache._client = MagicMock()
        # First scan (bar series) returns only AAPL keys; second (latest_bar) returns empty.
        cache._client.scan.side_effect = [
            (0, ["marketlens:bars:AAPL:1d", "marketlens:bars:AAPL:1h"]),
            (0, []),
        ]

        n = cache.invalidate_bars_for_symbol("AAPL")
        self.assertEqual(n, 2)
        # Verify SCAN was called with AAPL-specific patterns (not wildcards)
        scan_patterns = [call.kwargs["match"] for call in cache._client.scan.call_args_list]
        self.assertIn("marketlens:bars:AAPL:*", scan_patterns)
        self.assertIn("marketlens:latest_bar:AAPL:*", scan_patterns)
        # MSFT keys would not match the AAPL pattern — confirm we don't touch them
        self.assertNotIn("marketlens:bars:MSFT:*", scan_patterns)

    def test_invalidate_returns_zero_when_no_keys(self):
        """No cached keys → returns 0, no DELETE call."""
        cache = RedisCache()
        cache._client = MagicMock()
        cache._client.scan.return_value = (0, [])

        n = cache.invalidate_bars_for_symbol("AAPL")
        self.assertEqual(n, 0)
        cache._client.delete.assert_not_called()

    def test_invalidate_handles_paginated_scan(self):
        """SCAN may return keys in multiple pages (cursor != 0). All pages merged."""
        cache = RedisCache()
        cache._client = MagicMock()
        # Pattern 1: page 1 returns cursor=5, page 2 returns cursor=0 (done).
        # Pattern 2: page 1 returns cursor=0 immediately (done).
        cache._client.scan.side_effect = [
            (5, ["marketlens:bars:AAPL:1d"]),
            (0, ["marketlens:bars:AAPL:1h"]),
            (0, ["marketlens:latest_bar:AAPL:1m"]),
        ]

        n = cache.invalidate_bars_for_symbol("AAPL")
        self.assertEqual(n, 3)

    def test_invalidate_returns_zero_when_redis_unavailable(self):
        """If Redis is down, invalidate is a no-op and returns 0."""
        cache = RedisCache()
        cache._client = None  # disconnected

        n = cache.invalidate_bars_for_symbol("AAPL")
        self.assertEqual(n, 0)

    def test_invalidate_returns_zero_on_redis_error(self):
        """A SCAN failure doesn't crash the ingestion path — returns 0."""
        cache = RedisCache()
        cache._client = MagicMock()
        cache._client.scan.side_effect = ConnectionError("Redis went away")

        n = cache.invalidate_bars_for_symbol("AAPL")
        self.assertEqual(n, 0)


class TestBarQuoteJsonHelpers(unittest.TestCase):
    """The cache module exposes ``_bar_to_json`` / ``_bars_to_json`` /
    ``_quote_to_json`` helpers used by every Redis write path (cache
    set + pub/sub publish). They centralise the ``model_dump()`` +
    ``json.dumps(default=str)`` pattern that used to be duplicated in
    five places in cache.py and one in ws_router."""

    def test_bar_to_json_round_trip(self):
        bar = Bar(
            symbol="AAPL",
            timestamp=datetime(2025, 6, 1, 10, 0),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
            timeframe="1m",
            provider="yahoo_finance",
            data_status=DataStatus.LIVE,
        )
        s = _bar_to_json(bar)
        # Round-trip through the same data path Redis would use.
        restored = Bar.model_validate_json(s)
        self.assertEqual(restored.symbol, bar.symbol)
        self.assertEqual(restored.timeframe, bar.timeframe)
        self.assertEqual(restored.open, bar.open)

    def test_bars_to_json_round_trip(self):
        bars = [
            Bar(
                symbol="AAPL",
                timestamp=datetime(2025, 6, 1, 10, i),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=1000,
                timeframe="1m",
                provider="yahoo_finance",
                data_status=DataStatus.LIVE,
            )
            for i in range(3)
        ]
        s = _bars_to_json(bars)
        restored_list = [Bar.model_validate(b) for b in __import__("json").loads(s)]
        self.assertEqual(len(restored_list), 3)
        self.assertEqual(restored_list[0].symbol, "AAPL")

    def test_quote_to_json_round_trip(self):
        quote = Quote(
            symbol="AAPL",
            price=150.0,
            timestamp=datetime(2025, 6, 1, 10, 0),
            provider="yahoo_finance",
            data_status=DataStatus.DELAYED,
        )
        s = _quote_to_json(quote)
        restored = Quote.model_validate_json(s)
        self.assertEqual(restored.price, 150.0)
        self.assertEqual(restored.symbol, "AAPL")

    def test_bar_to_json_serializes_datetime(self):
        """datetime fields must serialize (default=str) — they are not
        natively JSON-serializable."""
        import json

        bar = Bar(
            symbol="AAPL",
            timestamp=datetime(2025, 6, 1, 10, 0, 30, 123456),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
            timeframe="1m",
            provider="yahoo_finance",
            data_status=DataStatus.LIVE,
        )
        s = _bar_to_json(bar)
        # No exception during json.dumps; payload is valid JSON.
        payload = json.loads(s)
        self.assertEqual(payload["symbol"], "AAPL")
        self.assertIn("timestamp", payload)


if __name__ == "__main__":
    unittest.main()
