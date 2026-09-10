"""
Phase 22 — Tests for /api/analysis/* endpoints:
  - GET /api/analysis/{symbol}/transitions
  - GET /api/analysis/{symbol}/support-resistance
  - GET /api/analysis/{symbol}/divergences
  - GET /api/analysis/{symbol}/bars
"""
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch


class TestTransitionsEndpoint(unittest.TestCase):

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


class TestSupportResistanceEndpoint(unittest.TestCase):

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
        mock_repo.get_bars.return_value = mock_bars

        client = TestClient(app)
        resp = client.get("/api/analysis/AAPL/support-resistance?timeframe=1d")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["symbol"], "AAPL")
        self.assertIn("levels", data)
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
        resp = client.get("/api/analysis/AAPL/support-resistance")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["levels"], [])
        self.assertEqual(data["count"], 0)


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
