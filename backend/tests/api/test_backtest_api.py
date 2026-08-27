"""
Tests for the Backtest API endpoints.
"""
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from fastapi.testclient import TestClient

from backend.api.main import app


def _mock_run(**kwargs):
    defaults = dict(
        id=1, symbol="AAPL", timeframe="1d",
        start_date=datetime(2025, 1, 1),
        end_date=datetime(2025, 12, 31),
        signals_requested="RSI_OVERSOLD,MACD_BULLISH",
        status="completed",
        total_bars=250, total_signals=12,
        win_rate_1d=0.55, avg_return_1d=1.2, avg_return_5d=3.4, avg_return_20d=8.1,
        error=None,
        created_at=datetime(2025, 12, 31, 12, 0, 0),
        completed_at=datetime(2025, 12, 31, 12, 0, 5),
    )
    defaults.update(kwargs)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def _mock_trade(**kwargs):
    defaults = dict(
        id=1, run_id=1, signal="RSI_OVERSOLD",
        entry_date=datetime(2025, 6, 1), entry_price=195.0,
        exit_date_1d=datetime(2025, 6, 2), exit_price_1d=197.0,
        return_1d=1.0256,
        exit_date_5d=datetime(2025, 6, 6), exit_price_5d=200.0,
        return_5d=2.5641,
        exit_date_20d=datetime(2025, 6, 27), exit_price_20d=205.0,
        return_20d=5.1282,
    )
    defaults.update(kwargs)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


class TestBacktestAPI(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        # Prevent engine startup from hitting the DB.
        self._engine_startup_patch = patch(
            "backend.alerts.engine.AlertsEngine.startup",
            return_value=None,
        )
        self._engine_startup_patch.start()
        self.addCleanup(self._engine_startup_patch.stop)

    # --- GET /api/backtest/ -----------------------------------------------

    def test_list_runs_empty(self):
        with patch("backend.api.backtest.router.BacktestRepository") as MockRepo:
            MockRepo.return_value.list_runs.return_value = []
            response = self.client.get("/api/backtest/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_list_runs_returns_runs(self):
        with patch("backend.api.backtest.router.BacktestRepository") as MockRepo:
            MockRepo.return_value.list_runs.return_value = [
                _mock_run(id=1), _mock_run(id=2),
            ]
            response = self.client.get("/api/backtest/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["symbol"], "AAPL")

    # --- POST /api/backtest/ ---------------------------------------------

    def test_create_backtest_success(self):
        with patch("backend.api.backtest.router.backtest_engine") as mock_engine, \
             patch("backend.api.backtest.router.BacktestRepository") as MockRepo:
            run = _mock_run(id=42, status="completed")
            # Engine returns just the run id; router re-fetches.
            mock_engine.run.return_value = 42
            MockRepo.return_value.get_run.return_value = run
            response = self.client.post("/api/backtest/", json={
                "symbol": "AAPL",
                "start_date": "2025-01-01T00:00:00",
                "end_date": "2025-12-31T00:00:00",
                "signals": ["RSI_OVERSOLD", "MACD_BULLISH"],
            })
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["id"], 42)
        mock_engine.run.assert_called_once()

    def test_create_backtest_defaults_signals_when_empty(self):
        with patch("backend.api.backtest.router.backtest_engine") as mock_engine, \
             patch("backend.api.backtest.router.BacktestRepository") as MockRepo:
            run = _mock_run()
            mock_engine.run.return_value = 1
            MockRepo.return_value.get_run.return_value = run
            response = self.client.post("/api/backtest/", json={
                "symbol": "TSLA",
                "start_date": "2025-01-01T00:00:00",
                "end_date": "2025-06-01T00:00:00",
            })
        self.assertEqual(response.status_code, 201)
        # Engine should have been called with a BacktestConfig whose
        # signals default to DEFAULT_SIGNALS when none were supplied.
        config = mock_engine.run.call_args.args[0]
        self.assertIn("RSI_OVERSOLD", config.signals)
        self.assertIn("HIGH_VOLUME", config.signals)

    def test_create_backtest_invalid_timeframe_returns_422(self):
        response = self.client.post("/api/backtest/", json={
            "symbol": "AAPL",
            "start_date": "2025-01-01T00:00:00",
            "end_date": "2025-12-31T00:00:00",
            "timeframe": "1h",
        })
        self.assertEqual(response.status_code, 422)

    def test_create_backtest_end_before_start_returns_422(self):
        response = self.client.post("/api/backtest/", json={
            "symbol": "AAPL",
            "start_date": "2025-12-31T00:00:00",
            "end_date": "2025-01-01T00:00:00",
        })
        self.assertEqual(response.status_code, 422)

    # --- GET /api/backtest/{id} -----------------------------------------

    def test_get_run_found(self):
        with patch("backend.api.backtest.router.BacktestRepository") as MockRepo:
            MockRepo.return_value.get_run.return_value = _mock_run(id=5)
            response = self.client.get("/api/backtest/5")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], 5)

    def test_get_run_not_found(self):
        with patch("backend.api.backtest.router.BacktestRepository") as MockRepo:
            MockRepo.return_value.get_run.return_value = None
            response = self.client.get("/api/backtest/999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Backtest run not found")

    # --- GET /api/backtest/{id}/trades ----------------------------------

    def test_get_trades_found(self):
        with patch("backend.api.backtest.router.BacktestRepository") as MockRepo:
            MockRepo.return_value.get_run.return_value = _mock_run()
            MockRepo.return_value.get_trades.return_value = [
                _mock_trade(id=1), _mock_trade(id=2, signal="MACD_BULLISH"),
            ]
            response = self.client.get("/api/backtest/1/trades")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)

    def test_get_trades_run_not_found(self):
        with patch("backend.api.backtest.router.BacktestRepository") as MockRepo:
            MockRepo.return_value.get_run.return_value = None
            response = self.client.get("/api/backtest/999/trades")
        self.assertEqual(response.status_code, 404)

    # --- DELETE /api/backtest/{id} --------------------------------------

    def test_delete_run_success(self):
        with patch("backend.api.backtest.router.BacktestRepository") as MockRepo:
            MockRepo.return_value.delete_run.return_value = True
            response = self.client.delete("/api/backtest/1")
        self.assertEqual(response.status_code, 204)

    def test_delete_run_not_found(self):
        with patch("backend.api.backtest.router.BacktestRepository") as MockRepo:
            MockRepo.return_value.delete_run.return_value = False
            response = self.client.delete("/api/backtest/999")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
