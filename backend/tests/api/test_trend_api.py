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
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.ttl_cache import _trend_cache
from backend.api.trend.router import _build_trend_payload, _trend_evidence
from backend.engines.timeframe import Timeframe
from backend.trend.trend_engine import TrendDirection, TrendSignal, TrendStrength
from backend.trend.trend_engine import ShortHorizonMomentum


def _make_signal(symbol: str, timeframe: Timeframe, direction: TrendDirection) -> TrendSignal:
    return TrendSignal(
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        strength=TrendStrength.MODERATE,
        confidence=0.75,
        timestamp=datetime(2025, 1, 1, 12, 0, 0),
    )


ET = ZoneInfo("America/New_York")


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

    def test_batch_declares_each_timeframe_scoring_profile(self):
        signals = {
            Timeframe.ONE_MINUTE: _make_signal("AAPL", Timeframe.ONE_MINUTE, TrendDirection.UPTREND),
            Timeframe.FIVE_MINUTE: _make_signal("AAPL", Timeframe.FIVE_MINUTE, TrendDirection.UPTREND),
            Timeframe.ONE_HOUR: _make_signal("AAPL", Timeframe.ONE_HOUR, TrendDirection.UPTREND),
        }
        self.mock_engine.get_current_trend.side_effect = lambda tf: signals[tf]

        response = self.client.get("/api/trend/AAPL/batch?timeframes=1m,5m,1h")

        self.assertEqual(response.status_code, 200)
        profiles = [row["scoring"] for row in response.json()]
        self.assertEqual(
            [profile["id"] for profile in profiles],
            ["directional_core", "intraday_directional", "full_technical"],
        )
        self.assertEqual([len(profile["components"]) for profile in profiles], [3, 4, 8])
        self.assertTrue(all(not profile["score_comparable_across_timeframes"] for profile in profiles))

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


class TestTrendEvidenceContract(unittest.TestCase):
    def setUp(self):
        self.engine = MagicMock()
        self.engine.get_bar_count.return_value = 50

    @staticmethod
    def _signal(timeframe: Timeframe, timestamp: datetime) -> TrendSignal:
        return TrendSignal(
            symbol="SPY",
            timeframe=timeframe,
            direction=TrendDirection.UPTREND,
            strength=TrendStrength.MODERATE,
            confidence=0.75,
            timestamp=timestamp,
        )

    @staticmethod
    def _metadata(timestamp: datetime, **overrides):
        return {
            "timestamp": timestamp,
            "data_status": "HISTORICAL",
            "provider": "webull",
            "session": "regular",
            "bar_closed": True,
            **overrides,
        }

    def test_live_evidence_uses_its_own_source_bar_timestamp(self):
        now = datetime(2026, 9, 24, 10, 0, 30, tzinfo=ET)
        source_timestamp = datetime(2026, 9, 24, 10, 0, 0, tzinfo=ET)
        signal = self._signal(Timeframe.ONE_MINUTE, now)

        evidence = _trend_evidence(
            self.engine,
            "1m",
            signal,
            self._metadata(source_timestamp),
            now=now,
        )

        self.assertEqual(evidence["freshness_state"], "live")
        self.assertTrue(evidence["valid"])
        self.assertEqual(evidence["age_seconds"], 30)
        self.assertTrue(evidence["source_as_of"].startswith("2026-09-24T10:00:00"))

    def test_daily_payload_uses_regular_close_not_later_signal_timestamp(self):
        daily_source = datetime(2026, 9, 24, 0, 0, 0, tzinfo=ET)
        later_signal_time = datetime(2026, 9, 24, 19, 59, 59, tzinfo=ET)
        signal = self._signal(Timeframe.ONE_DAY, later_signal_time)
        self.engine.get_current_trend.return_value = signal
        self.engine.get_timeframe_metadata.return_value = self._metadata(daily_source)

        payload = _build_trend_payload(self.engine, "SPY", "1d", Timeframe.ONE_DAY)

        self.assertTrue(payload["timestamp"].startswith("2026-09-24T16:00:00"))
        self.assertTrue(payload["evidence"]["source_timestamp"].startswith("2026-09-24T00:00:00"))
        self.assertTrue(payload["evidence"]["source_as_of"].startswith("2026-09-24T16:00:00"))

    def test_short_timeframe_payload_exposes_measured_momentum_separately_from_strength(self):
        source = datetime(2026, 9, 24, 10, 0, 0, tzinfo=ET)
        signal = self._signal(Timeframe.ONE_MINUTE, source)
        signal.short_horizon_momentum = ShortHorizonMomentum.PERSISTENT
        signal.short_horizon_momentum_score = 0.92
        self.engine.get_current_trend.return_value = signal
        self.engine.get_timeframe_metadata.return_value = self._metadata(source)

        payload = _build_trend_payload(self.engine, "SPY", "1m", Timeframe.ONE_MINUTE)

        self.assertEqual(payload["strength"], "moderate")
        self.assertEqual(payload["short_horizon_momentum"], "persistent")
        self.assertEqual(payload["short_horizon_momentum_score"], 0.92)

    def test_derived_weekly_payload_uses_week_close_not_bucket_start(self):
        # The stored weekly key is Monday 00:00 UTC.  Its decision-useful
        # as-of time is the completed Friday regular-session close.
        weekly_bucket = datetime(2026, 9, 14, 0, 0, 0, tzinfo=ZoneInfo("UTC"))
        signal = self._signal(Timeframe.ONE_WEEK, datetime(2026, 9, 19, 20, 0, tzinfo=ZoneInfo("UTC")))
        self.engine.get_current_trend.return_value = signal
        self.engine.get_timeframe_metadata.return_value = self._metadata(
            weekly_bucket,
            provider="aggregated_from_1d",
        )

        payload = _build_trend_payload(self.engine, "SPY", "1wk", Timeframe.ONE_WEEK)

        self.assertTrue(payload["timestamp"].startswith("2026-09-18T16:00:00"))
        self.assertTrue(payload["evidence"]["source_timestamp"].startswith("2026-09-13T20:00:00"))
        self.assertTrue(payload["evidence"]["source_as_of"].startswith("2026-09-18T16:00:00"))

    def test_incomplete_derived_bar_is_unavailable_even_when_market_is_closed(self):
        now = datetime(2026, 9, 24, 21, 0, 0, tzinfo=ET)
        signal = self._signal(Timeframe.ONE_WEEK, datetime(2026, 9, 21, 0, 0, 0, tzinfo=ET))

        evidence = _trend_evidence(
            self.engine,
            "1wk",
            signal,
            self._metadata(signal.timestamp, data_status="INCOMPLETE", provider="aggregated_from_1d"),
            now=now,
        )

        self.assertEqual(evidence["freshness_state"], "unavailable")
        self.assertFalse(evidence["valid"])
        self.assertEqual(evidence["invalid_reason"], "data_status_incomplete")

    def test_missing_source_timestamp_is_unavailable_not_signal_timestamp_fallback(self):
        now = datetime(2026, 9, 24, 10, 0, 0, tzinfo=ET)
        signal = self._signal(Timeframe.ONE_MINUTE, now)

        evidence = _trend_evidence(
            self.engine,
            "1m",
            signal,
            self._metadata(None),
            now=now,
        )

        self.assertEqual(evidence["freshness_state"], "unavailable")
        self.assertFalse(evidence["valid"])
        self.assertEqual(evidence["invalid_reason"], "source_timestamp_missing")
        self.assertIsNone(evidence["source_as_of"])

    def test_delayed_provider_data_is_unavailable(self):
        now = datetime(2026, 9, 24, 10, 0, 0, tzinfo=ET)
        signal = self._signal(Timeframe.FIVE_MINUTE, now)

        evidence = _trend_evidence(
            self.engine,
            "5m",
            signal,
            self._metadata(now, data_status="DELAYED"),
            now=now,
        )

        self.assertEqual(evidence["freshness_state"], "unavailable")
        self.assertFalse(evidence["valid"])
        self.assertEqual(evidence["invalid_reason"], "data_status_delayed")

    def test_closed_market_last_completed_bar_is_valid_closed_session(self):
        now = datetime(2026, 9, 24, 21, 0, 0, tzinfo=ET)
        source_timestamp = datetime(2026, 9, 24, 19, 59, 0, tzinfo=ET)
        signal = self._signal(Timeframe.ONE_MINUTE, source_timestamp)

        evidence = _trend_evidence(
            self.engine,
            "1m",
            signal,
            self._metadata(source_timestamp, session="after_hours"),
            now=now,
        )

        self.assertEqual(evidence["freshness_state"], "closed_session")
        self.assertTrue(evidence["valid"])
        self.assertIsNone(evidence["invalid_reason"])

    def test_insufficient_warmup_is_warming_not_an_ordinary_valid_signal(self):
        self.engine.get_bar_count.return_value = 12
        now = datetime(2026, 9, 24, 10, 0, 0, tzinfo=ET)
        signal = self._signal(Timeframe.FIVE_MINUTE, now)

        evidence = _trend_evidence(
            self.engine,
            "5m",
            signal,
            self._metadata(now),
            now=now,
        )

        self.assertEqual(evidence["freshness_state"], "warming")
        self.assertFalse(evidence["valid"])
        self.assertEqual(evidence["warmup_bars"], 12)
        self.assertEqual(evidence["required_warmup_bars"], 50)

    def test_open_market_old_intraday_source_is_stale(self):
        now = datetime(2026, 9, 24, 10, 30, 0, tzinfo=ET)
        source_timestamp = datetime(2026, 9, 24, 10, 20, 0, tzinfo=ET)
        signal = self._signal(Timeframe.ONE_MINUTE, source_timestamp)

        evidence = _trend_evidence(
            self.engine,
            "1m",
            signal,
            self._metadata(source_timestamp),
            now=now,
        )

        self.assertEqual(evidence["freshness_state"], "stale")
        self.assertFalse(evidence["valid"])
        self.assertEqual(evidence["invalid_reason"], "source_stale")


if __name__ == "__main__":
    unittest.main()
