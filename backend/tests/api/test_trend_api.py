"""
Tests for the Trend API endpoints, focused on the batch endpoint added to
collapse the dashboard's per-timeframe GET fan-out into one request.

The shared TrendEngine registry is mocked at the import boundary
(``backend.api.trend.router.get_engine``) so the endpoints are exercised
in isolation. The real engine is covered by
``backend/tests/trend/test_trend_engine.py``.
"""

import os
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.ttl_cache import _trend_cache
from backend.engines.timeframe import Timeframe
from backend.trend.trend_engine import TrendDirection, TrendSignal, TrendStrength


def _make_signal(symbol: str, timeframe: Timeframe, direction: TrendDirection) -> TrendSignal:
    return TrendSignal(
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        strength=TrendStrength.MODERATE,
        confidence=0.75,
        timestamp=datetime(2025, 1, 1, 12, 0, 0),
    )


class TestTrendBatchAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        _trend_cache.clear()
        self.engine_patch = patch("backend.api.trend.router.get_engine")
        self.mock_get_engine = self.engine_patch.start()
        self.mock_engine = MagicMock()
        self.mock_get_engine.return_value = self.mock_engine

    def tearDown(self):
        self.engine_patch.stop()
        _trend_cache.clear()

    def test_batch_returns_one_entry_per_requested_timeframe_in_order(self):
        signals = {
            Timeframe.FIVE_MINUTE: _make_signal(
                "AAPL", Timeframe.FIVE_MINUTE, TrendDirection.UPTREND
            ),
            Timeframe.ONE_HOUR: _make_signal("AAPL", Timeframe.ONE_HOUR, TrendDirection.DOWNTREND),
            Timeframe.ONE_DAY: _make_signal("AAPL", Timeframe.ONE_DAY, TrendDirection.SIDEWAYS),
        }
        self.mock_engine.get_current_trend.side_effect = lambda tf: signals[tf]

        response = self.client.get("/api/trend/AAPL/batch?timeframes=5m,1h,1d")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([r["timeframe"] for r in body], ["5m", "1h", "1d"])
        self.assertEqual(
            [r["direction"] for r in body],
            ["uptrend", "downtrend", "sideways"],
        )
        # One shared engine lookup, not one per timeframe.
        self.mock_get_engine.assert_called_once_with("AAPL")

    def test_batch_matches_single_timeframe_endpoint(self):
        """The batch and single-timeframe endpoints must agree — the batch
        endpoint reuses the same cache-and-build path."""
        signal = _make_signal("MSFT", Timeframe.FIFTEEN_MINUTE, TrendDirection.UPTREND)
        self.mock_engine.get_current_trend.return_value = signal

        single = self.client.get("/api/trend/MSFT/current/15m")
        _trend_cache.clear()  # isolate the batch call from the single call's cache write
        batch = self.client.get("/api/trend/MSFT/batch?timeframes=15m")

        self.assertEqual(single.json(), batch.json()[0])

    def test_batch_unknown_timeframe_returns_placeholder_not_error(self):
        """A timeframe the engine has no signal for yet (cold start) still
        returns a well-formed entry, not an exception — mirrors the
        single-timeframe endpoint's None handling."""
        self.mock_engine.get_current_trend.return_value = None

        response = self.client.get("/api/trend/AAPL/batch?timeframes=1wk")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["direction"], "unknown")
        self.assertIsNone(body[0]["timestamp"])

    def test_batch_invalid_timeframe_returns_400(self):
        response = self.client.get("/api/trend/AAPL/batch?timeframes=5m,not_a_tf")
        self.assertEqual(response.status_code, 400)

    def test_batch_empty_timeframes_returns_400(self):
        response = self.client.get("/api/trend/AAPL/batch?timeframes=")
        self.assertEqual(response.status_code, 400)

    def test_batch_reuses_cache_across_calls(self):
        """A timeframe already cached by a prior request is served from
        cache, not recomputed — same TTL-cache contract as the
        single-timeframe endpoint."""
        signal = _make_signal("AAPL", Timeframe.ONE_HOUR, TrendDirection.UPTREND)
        self.mock_engine.get_current_trend.return_value = signal

        first = self.client.get("/api/trend/AAPL/batch?timeframes=1h")
        self.assertEqual(self.mock_engine.get_current_trend.call_count, 1)

        second = self.client.get("/api/trend/AAPL/batch?timeframes=1h")
        # No additional engine reads — served from the TTL cache.
        self.assertEqual(self.mock_engine.get_current_trend.call_count, 1)
        self.assertEqual(first.json(), second.json())


if __name__ == "__main__":
    unittest.main()
