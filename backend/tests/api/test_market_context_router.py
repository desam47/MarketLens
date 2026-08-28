"""
Phase 22 — Tests for /api/market-context/* endpoints:
  - GET /api/market-context/current
  - GET /api/market-context/history
"""
import unittest
from unittest.mock import MagicMock, patch


class TestMarketContextCurrent(unittest.TestCase):

    @patch("backend.api.market_context.router.get_engine")
    def test_returns_current_regime(self, mock_get_engine):
        from fastapi.testclient import TestClient

        from backend.api.main import app

        mock_engine = MagicMock()
        # Router calls engine.get_current_context() → to_dict()
        mock_engine.get_current_context.return_value = MagicMock(
            to_dict=MagicMock(return_value={
                "regime": "bullish",
                "confidence": 0.82,
                "trend_strength": 0.7,
                "momentum": 0.5,
                "volatility_state": "normal",
                "sub_regimes": {},
                "contributing_factors": {},
                "timestamp": "2024-01-01T00:00:00Z",
            })
        )
        mock_get_engine.return_value = mock_engine

        client = TestClient(app)
        resp = client.get("/api/market-context/current")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["regime"], "bullish")
        self.assertAlmostEqual(data["confidence"], 0.82)

    @patch("backend.api.market_context.router.get_engine")
    def test_engine_initializes_on_first_call(self, mock_get_engine):
        from fastapi.testclient import TestClient

        from backend.api.main import app

        mock_engine = MagicMock()
        mock_engine.get_current_context.return_value = MagicMock(
            to_dict=MagicMock(return_value={
                "regime": "bearish", "confidence": 0.75,
                "trend_strength": 0.6, "momentum": -0.3,
                "volatility_state": "high",
                "sub_regimes": {}, "contributing_factors": {},
                "timestamp": "2024-01-01T00:00:00Z",
            })
        )
        mock_get_engine.return_value = mock_engine

        client = TestClient(app)
        resp = client.get("/api/market-context/current")
        self.assertEqual(resp.status_code, 200)
        mock_get_engine.assert_called_once()


class TestMarketContextHistory(unittest.TestCase):

    @patch("backend.api.market_context.router.get_engine")
    def test_returns_history(self, mock_get_engine):
        from fastapi.testclient import TestClient

        from backend.api.main import app

        mock_engine = MagicMock()
        mock_hist = [
            MagicMock(
                to_dict=MagicMock(return_value={
                    "regime": "bullish",
                    "confidence": 0.8,
                    "trend_strength": 0.7,
                    "momentum": 0.5,
                    "volatility_state": "normal",
                    "sub_regimes": {},
                    "contributing_factors": {},
                    "timestamp": f"2024-01-{i:02d}T00:00:00Z",
                })
            )
            for i in range(1, 11)
        ]
        mock_engine.get_history.return_value = mock_hist
        mock_get_engine.return_value = mock_engine

        client = TestClient(app)
        resp = client.get("/api/market-context/history?limit=10")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 10)
        self.assertEqual(len(data["history"]), 10)

    @patch("backend.api.market_context.router.get_engine")
    def test_empty_history(self, mock_get_engine):
        from fastapi.testclient import TestClient

        from backend.api.main import app

        mock_engine = MagicMock()
        mock_engine.get_history.return_value = []
        mock_get_engine.return_value = mock_engine

        client = TestClient(app)
        resp = client.get("/api/market-context/history")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["history"], [])
        self.assertEqual(data["count"], 0)

    @patch("backend.api.market_context.router.get_engine")
    def test_500_on_engine_error(self, mock_get_engine):
        from fastapi.testclient import TestClient

        from backend.api.main import app

        mock_get_engine.side_effect = RuntimeError("engine init failed")

        client = TestClient(app)
        resp = client.get("/api/market-context/current")
        self.assertEqual(resp.status_code, 500)


if __name__ == "__main__":
    unittest.main()
