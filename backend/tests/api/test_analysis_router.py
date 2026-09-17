"""
Phase 22 — Tests for /api/analysis/* endpoints:
  - GET /api/analysis/{symbol}/transitions
  - GET /api/analysis/{symbol}/price-range
  - GET /api/analysis/{symbol}/divergences
  - GET /api/analysis/{symbol}/bars
"""
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from backend.api.analysis.router import _to_dashboard_tz, _transitions_cache


def _make_bar(close: float, ts: datetime) -> dict:
    """Mimic load_bars() output: a flat dict with OHLCV + timestamp."""
    return {
        "open": close, "high": close + 1.0, "low": close - 1.0,
        "close": close, "volume": 1_000_000, "timestamp": ts,
        "source": "historical", "data_status": "historical",
    }


class TestTransitionsEndpoint(unittest.TestCase):

    def setUp(self):
        # In-memory 30s-TTL cache keyed by symbol/timeframe/params; clear it so
        # each test exercises fresh logic rather than a stale cached payload.
        _transitions_cache.clear()

    @patch("backend.analysis.series.bar_repository")
    def test_returns_transitions(self, mock_repo):
        from fastapi.testclient import TestClient

        from backend.api.main import app

        # Minimal bar set: 25 bars so sma_window=20 is satisfied
        mock_bars = [
            MagicMock(
                open=100.0 + i, high=101.0 + i, low=99.0 + i,
                close=100.5 + i, volume=1_000_000,
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            )
            for i in range(25)
        ]
        mock_repo.get_bars.return_value = mock_bars

        client = TestClient(app)
        resp = client.get("/api/analysis/AAPL/transitions?timeframe=1d&window=5")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["symbol"], "AAPL")
        self.assertIn("transitions", data)
        self.assertIn("latest_score", data)

    @patch("backend.api.analysis.router._load_bars")
    def test_newest_bar_is_latest_transition(self, mock_load_bars):
        """Regresses the ordering bug where bars (newest→oldest from
        desc=True) were fed straight into the temporal z-score + transition
        windows. That inverted the lookback so the newest bar could never be a
        transition's current point and the reported latest transition lagged by
        `window` bars (e.g. a stale Aug-6 card while the data ran to Sep-11).

        With a gentle uptrend then a sharp upward spike, the newest transition
        must land ON the newest bar and `latest_timestamp` must equal the
        newest bar.
        """
        # 30 daily bars, oldest→newest. A gentle uptrend for the first 25
        # bars (so the z-score score is nonzero — a perfectly flat lead-in
        # scores exactly 0.0, which the engine treats as neutral per the
        # zero-is-neutral rule), then a sharp spike on the last few bars so the
        # newest bar both scores strongly positive AND becomes a transition's
        # "current" point anchored on the newest bar.
        base = datetime(2024, 1, 1, tzinfo=UTC)
        closes = [100.0 + i * 0.5 for i in range(25)] + [140.0, 180.0, 220.0, 260.0, 300.0]
        bars_old_to_new = [
            _make_bar(c, base + timedelta(days=i)) for i, c in enumerate(closes)
        ]
        # load_bars returns desc=True (newest→oldest), as the real repo does.
        mock_load_bars.return_value = list(reversed(bars_old_to_new))

        from fastapi.testclient import TestClient

        from backend.api.analysis.router import _to_dashboard_tz
        from backend.api.main import app

        client = TestClient(app)
        resp = client.get("/api/analysis/AAPL/transitions?timeframe=1d&window=5")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # _to_dashboard_tz returns an already-formatted dashboard (NY) tz
        # string, so compare the header against it directly.
        newest_ts = _to_dashboard_tz(base + timedelta(days=29))

        # Header "Updated" must point at the newest bar, not the oldest.
        self.assertEqual(data["latest_timestamp"], newest_ts)

        self.assertTrue(data["transitions"], "expected at least one transition")
        # Newest transition (index 0) must be anchored on the newest bar.
        # to_dict() emits the raw (UTC) bar timestamp, so compare against that
        # directly rather than the NY-converted header value.
        newest_ts_utc = (base + timedelta(days=29)).isoformat()
        self.assertEqual(data["transitions"][0]["timestamp"], newest_ts_utc)
        # Its current score must be positive (the spike), not the old flat 0/neg.
        self.assertGreater(data["transitions"][0]["current_score"], 0)

    @patch("backend.analysis.series.bar_repository")
    def test_insufficient_bars_returns_empty(self, mock_repo):
        from fastapi.testclient import TestClient

        from backend.api.main import app

        mock_repo.get_bars.return_value = [MagicMock(
            open=100, high=101, low=99, close=100.5, volume=1_000_000,
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        )]

        client = TestClient(app)
        resp = client.get("/api/analysis/AAPL/transitions?timeframe=1d&window=5")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["transitions"], [])
        self.assertEqual(data["count"], 0)

    @patch("backend.analysis.series.bar_repository")
    def test_symbol_uppercased(self, mock_repo):
        from fastapi.testclient import TestClient

        from backend.api.main import app

        mock_repo.get_bars.return_value = []

        client = TestClient(app)
        resp = client.get("/api/analysis/aapl/transitions")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["symbol"], "AAPL")

    @patch("backend.analysis.series.bar_repository")
    def test_500_on_repo_error(self, mock_repo):
        from fastapi.testclient import TestClient

        from backend.api.main import app

        mock_repo.get_bars.side_effect = RuntimeError("DB error")

        client = TestClient(app)
        resp = client.get("/api/analysis/AAPL/transitions")
        self.assertEqual(resp.status_code, 500)


class TestPriceRangeEndpoint(unittest.TestCase):

    @patch("backend.analysis.series.bar_repository")
    def test_returns_levels(self, mock_repo):
        from fastapi.testclient import TestClient

        from backend.api.main import app

        mock_bars = [
            MagicMock(
                open=100.0, high=101.0, low=99.0,
                close=100.5, volume=1_000_000,
                timestamp=datetime(2024, 1, i + 1, tzinfo=UTC),
            )
            for i in range(30)
        ]
        mock_repo.get_bars.return_value = list(reversed(mock_bars))

        client = TestClient(app)
        resp = client.get("/api/analysis/AAPL/price-range?timeframe=1d")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["symbol"], "AAPL")
        self.assertIn("levels", data)
        self.assertIn("count", data)
        self.assertEqual(
            data["latest_close_timestamp"],
            _to_dashboard_tz(mock_bars[-1].timestamp),
        )
        self.assertIsNotNone(data["fetched_at"])

    @patch("backend.analysis.series.bar_repository")
    def test_insufficient_bars_returns_empty(self, mock_repo):
        from fastapi.testclient import TestClient

        from backend.api.main import app

        mock_repo.get_bars.return_value = [MagicMock(
            open=100, high=101, low=99, close=100.5, volume=1_000_000,
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        )]

        client = TestClient(app)
        resp = client.get("/api/analysis/AAPL/price-range")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["levels"], [])
        self.assertEqual(data["count"], 0)
        self.assertIsNotNone(data["latest_close_timestamp"])
        self.assertIsNotNone(data["fetched_at"])


class TestDivergencesEndpoint(unittest.TestCase):

    @patch("backend.analysis.series.bar_repository")
    def test_returns_divergences(self, mock_repo):
        from fastapi.testclient import TestClient

        from backend.api.main import app

        mock_bars = [
            MagicMock(
                open=100.0, high=101.0, low=99.0,
                close=100.0 + i * 0.1, volume=1_000_000,
                timestamp=datetime(2024, 1, (i % 28) + 1, tzinfo=UTC),
            )
            for i in range(40)
        ]
        mock_repo.get_bars.return_value = mock_bars

        client = TestClient(app)
        resp = client.get("/api/analysis/AAPL/divergences?timeframe=1d")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["symbol"], "AAPL")
        self.assertIn("divergences", data)
        self.assertIn("count", data)

    @patch("backend.analysis.series.bar_repository")
    def test_insufficient_bars_returns_empty(self, mock_repo):
        from fastapi.testclient import TestClient

        from backend.api.main import app

        mock_repo.get_bars.return_value = [MagicMock(
            open=100, high=101, low=99, close=100.5, volume=1_000_000,
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        )]

        client = TestClient(app)
        resp = client.get("/api/analysis/AAPL/divergences")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["divergences"], [])
        self.assertEqual(data["count"], 0)

    @patch("backend.api.analysis.router._macd_histogram_series")
    @patch("backend.api.analysis.router._rsi_series")
    @patch("backend.api.analysis.router._load_bars")
    def test_newest_divergence_is_first_and_in_order(
        self, mock_load_bars, mock_rsi, mock_macd
    ):
        """Regresses the time-ordering bug where bars (newest→oldest from
        desc=True) were fed straight into the DivergenceEngine, which assumes
        chronological (older pivot = a, newer = b) order. That inverted every
        pivot pair and attached the OLDER bar's timestamp, so the returned
        list came back oldest-first instead of newest-first — and the
        frontend sliced the 15 OLDEST divergences.

        Build a series with three clean swing highs (chronological indices
        15, 25, 35) and a monotonically DECREASING RSI. That forces two bearish
        RSI divergences: (15→25) and (25→35). With the fix the payload is
        newest-first, so index 0 must be anchored on the newest bar (index 35)
        and the timestamps must descend. The bug returned them ascending.
        """
        n = 40
        base = datetime(2024, 1, 1, tzinfo=UTC)
        closes = [100.0 + i for i in range(n)]

        def _high(i: int) -> float:
            # Strict local maxima (swing highs) only at 15 / 25 / 35.
            if i == 15:
                return closes[i] + 4.0
            if i == 25:
                return closes[i] + 6.0
            if i == 35:
                return closes[i] + 8.0
            return float(closes[i])

        highs = [_high(i) for i in range(n)]
        # Strictly increasing lows → no swing lows → no bullish divergences.
        lows = [c - 1.0 for c in closes]
        volumes = [1_000_000.0] * n
        timestamps = [base + timedelta(days=i) for i in range(n)]

        bars_old_to_new = [
            {
                "open": closes[i], "high": highs[i], "low": lows[i],
                "close": closes[i], "volume": volumes[i],
                "timestamp": timestamps[i],
                "source": "historical", "data_status": "historical",
            }
            for i in range(n)
        ]
        # load_bars returns desc=True (newest→oldest), as the real repo does.
        mock_load_bars.return_value = list(reversed(bars_old_to_new))

        # Patched indicators: RSI strictly decreasing yields bearish RSI
        # divergences at every consecutive swing-high pair; MACD constant →
        # no MACD divergences; volumes constant → no volume divergences.
        mock_rsi.return_value = [100.0 - i for i in range(n)]
        mock_macd.return_value = [0.0] * n

        from fastapi.testclient import TestClient

        from backend.api.main import app

        client = TestClient(app)
        resp = client.get("/api/analysis/AAPL/divergences?timeframe=1d")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        divs = data["divergences"]
        self.assertTrue(divs, "expected at least one divergence")

        # Newest-first: index 0 must be the most recent divergence, anchored
        # on the newest bar (chronological index 35).
        newest_ts = (base + timedelta(days=35)).isoformat()
        self.assertEqual(divs[0]["timestamp"], newest_ts)
        self.assertEqual(divs[0]["pivot_b_index"], 35)

        # The whole list must be in descending (newest→oldest) timestamp
        # order. The bug returned it ascending (oldest first).
        seen = [d["timestamp"] for d in divs]
        self.assertEqual(
            seen, sorted(seen, reverse=True),
            "divergences must be ordered newest-first",
        )


class TestBarsEndpoint(unittest.TestCase):

    @patch("backend.analysis.series.bar_repository")
    def test_returns_bars(self, mock_repo):
        from fastapi.testclient import TestClient

        from backend.api.main import app

        mock_bars = [
            MagicMock(
                open=100.0, high=101.0, low=99.0,
                close=100.5, volume=1_000_000,
                timestamp=datetime(2024, 1, i + 1, tzinfo=UTC),
            )
            for i in range(10)
        ]
        mock_repo.get_bars.return_value = mock_bars

        client = TestClient(app)
        resp = client.get("/api/analysis/AAPL/bars?timeframe=1d&limit=10")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["symbol"], "AAPL")
        self.assertEqual(data["count"], 10)
        self.assertEqual(len(data["bars"]), 10)
        # Each bar should have OHLCV + timestamp
        bar = data["bars"][0]
        self.assertIn("open", bar)
        self.assertIn("high", bar)
        self.assertIn("low", bar)
        self.assertIn("close", bar)
        self.assertIn("volume", bar)

    @patch("backend.analysis.series.bar_repository")
    def test_empty_bars(self, mock_repo):
        from fastapi.testclient import TestClient

        from backend.api.main import app

        mock_repo.get_bars.return_value = []

        client = TestClient(app)
        resp = client.get("/api/analysis/AAPL/bars")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["bars"], [])
        self.assertEqual(data["count"], 0)


if __name__ == "__main__":
    unittest.main()
