"""
Unit tests for ``backend.backtesting.engine`` helpers and state machine.

The pure helpers (``_compute_metrics``, ``_build_trade``,
``_relative_volume``) are tested directly. The state machine is tested
by patching the bar repository so the engine never touches the real DB.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.backtesting.engine import (
    _build_trade,
    _compute_metrics,
    BacktestConfig,
    BacktestEngine,
)
from backend.models.market_data import Bar, DataStatus


def _bar(close, volume=1_000_000, days_ago=0):
    ts = datetime(2025, 1, 1) + timedelta(days=days_ago)
    return Bar(
        symbol="AAPL", timestamp=ts, open=close, high=close * 1.01,
        low=close * 0.99, close=close, volume=volume,
        timeframe="1d", provider="test", data_status=DataStatus.LIVE,
    )


def _mock_trade(signal, entry_price, return_1d, return_5d, return_20d):
    t = MagicMock()
    t.signal = signal
    t.entry_date = datetime(2025, 1, 15)
    t.entry_price = entry_price
    t.return_1d = return_1d
    t.return_5d = return_5d
    t.return_20d = return_20d
    return t


class TestComputeMetrics(unittest.TestCase):

    def test_empty_trades_returns_none_metrics(self):
        result = _compute_metrics([])
        self.assertIsNone(result["win_rate_1d"])
        self.assertIsNone(result["avg_return_1d"])
        self.assertIsNone(result["avg_return_5d"])
        self.assertIsNone(result["avg_return_20d"])

    def test_win_rate_half(self):
        trades = [
            _mock_trade("RSI_OVERSOLD", 100, 2.0, 5.0, 10.0),
            _mock_trade("RSI_OVERSOLD", 100, -2.0, 1.0, -3.0),
        ]
        result = _compute_metrics(trades)
        self.assertEqual(result["win_rate_1d"], 0.5)

    def test_win_rate_all_winners(self):
        trades = [_mock_trade("MACD_BULLISH", 100, 1.0, 2.0, 5.0)]
        result = _compute_metrics(trades)
        self.assertEqual(result["win_rate_1d"], 1.0)

    def test_avg_returns_computed(self):
        trades = [
            _mock_trade("RSI_OVERSOLD", 100, 2.0, 4.0, 8.0),
            _mock_trade("RSI_OVERSOLD", 100, 4.0, 6.0, 12.0),
        ]
        result = _compute_metrics(trades)
        self.assertAlmostEqual(result["avg_return_1d"], 3.0)
        self.assertAlmostEqual(result["avg_return_5d"], 5.0)
        self.assertAlmostEqual(result["avg_return_20d"], 10.0)

    def test_none_return_1d_skipped_from_avg(self):
        t = _mock_trade("RSI_OVERSOLD", 100, None, 5.0, 10.0)
        result = _compute_metrics([t])
        self.assertIsNone(result["avg_return_1d"])


class TestBuildTrade(unittest.TestCase):

    def test_insufficient_forward_bars_returns_none(self):
        # Only 5 bars total — can't compute the 20d return.
        bars = [_bar(100.0 + i, days_ago=4 - i) for i in range(5)]
        trade = _build_trade(signal="RSI_OVERSOLD", bars=bars, entry_index=4)
        self.assertIsNone(trade)

    def test_valid_trade_populates_all_windows(self):
        # Build 35 bars (enough for 14-bar warmup + 20-bar forward).
        bars = [_bar(100.0 + i, days_ago=34 - i) for i in range(35)]
        trade = _build_trade(signal="RSI_OVERSOLD", bars=bars, entry_index=14)
        self.assertIsNotNone(trade)
        self.assertEqual(trade.signal, "RSI_OVERSOLD")
        self.assertEqual(trade.entry_price, 114.0)
        self.assertIsNotNone(trade.return_1d)
        self.assertIsNotNone(trade.return_5d)
        self.assertIsNotNone(trade.return_20d)
        self.assertIsNotNone(trade.exit_date_1d)
        self.assertIsNotNone(trade.exit_date_5d)
        self.assertIsNotNone(trade.exit_date_20d)

    def test_return_1d_positive_when_price_rises(self):
        bars = [_bar(100.0 + i, days_ago=34 - i) for i in range(35)]
        trade = _build_trade(signal="RSI_OVERSOLD", bars=bars, entry_index=14)
        self.assertGreater(trade.return_1d, 0)
        self.assertGreater(trade.return_5d, 0)
        self.assertGreater(trade.return_20d, 0)

    def test_return_1d_negative_when_price_falls(self):
        bars = [_bar(100.0 - i, days_ago=34 - i) for i in range(35)]
        trade = _build_trade(signal="RSI_OVERSOLD", bars=bars, entry_index=14)
        self.assertLess(trade.return_1d, 0)
        self.assertLess(trade.return_5d, 0)
        self.assertLess(trade.return_20d, 0)


class TestEngineStateMachine(unittest.TestCase):
    """Test the run() method's state transitions."""

    def _mock_bar_repo(self, bars):
        m = MagicMock()
        # Simulate get_bars returning oldest→newest.
        m.return_value = bars
        return m

    def _mock_repo(self):
        m = MagicMock()
        # create_run returns a mock BacktestRun with an id.
        run = MagicMock()
        run.id = 1
        m.create_run.return_value = run
        m.update_run_status.return_value = run
        m.get_run.return_value = run
        return m

    def test_insufficient_bars_marks_failed(self):
        # Fewer than 34 bars → insufficient history.
        bars = [_bar(100.0, days_ago=5 - i) for i in range(5)]
        engine = BacktestEngine()

        with patch("backend.backtesting.engine.BacktestRepository") as MockRepo, \
             patch.object(BacktestEngine, "_load_bars", return_value=bars):

            MockRepo.return_value = self._mock_repo()

            config = BacktestConfig(
                symbol="AAPL",
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 6, 1),
                signals=["RSI_OVERSOLD"],
            )
            result = engine.run(config)

            # run() returns the run_id as an int.
            self.assertEqual(result, 1)
            # update_run_status should have been called with status="failed".
            calls = MockRepo.return_value.update_run_status.call_args_list
            self.assertTrue(
                any(c.kwargs.get("status") == "failed" for c in calls),
                f"Expected 'failed' in update_run_status calls, got: {calls}",
            )

    def test_sufficient_bars_marks_completed(self):
        # 50 bars: enough for warmup + forward windows.
        bars = [_bar(100.0 + i, days_ago=49 - i) for i in range(50)]
        engine = BacktestEngine()

        # Build a stub scanner that fires RSI_OVERSOLD on every bar.
        stub_scanner = MagicMock()
        stub_result = MagicMock()
        stub_result.signals = []
        stub_scanner._generate_signals = MagicMock(
            side_effect=lambda r: setattr(r, "signals", ["RSI_OVERSOLD"])
        )

        with patch("backend.backtesting.engine.BacktestRepository") as MockRepo, \
             patch("backend.backtesting.engine._build_scanner") as mock_build_scanner, \
             patch.object(BacktestEngine, "_load_bars", return_value=bars):

            mock_build_scanner.return_value = stub_scanner
            MockRepo.return_value = self._mock_repo()

            config = BacktestConfig(
                symbol="AAPL",
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 12, 31),
                signals=["RSI_OVERSOLD"],
            )
            result = engine.run(config)

            # run() returns the run_id as an int.
            self.assertEqual(result, 1)
            # update_run_status should have been called with status="completed".
            calls = MockRepo.return_value.update_run_status.call_args_list
            self.assertTrue(
                any(c.kwargs.get("status") == "completed" for c in calls),
                f"Expected 'completed' in update_run_status calls, got: {calls}",
            )

    def test_unknown_signal_not_recorded(self):
        # Scanner fires RSI_OVERSOLD but config requests MACD_BULLISH → no trades.
        bars = [_bar(100.0 + i, days_ago=49 - i) for i in range(50)]
        engine = BacktestEngine()

        stub_scanner = MagicMock()
        stub_scanner._generate_signals = MagicMock(
            side_effect=lambda r: setattr(r, "signals", ["RSI_OVERSOLD"])
        )

        with patch("backend.backtesting.engine.BacktestRepository") as MockRepo, \
             patch("backend.backtesting.engine._build_scanner") as mock_build_scanner, \
             patch.object(BacktestEngine, "_load_bars", return_value=bars):

            mock_build_scanner.return_value = stub_scanner
            repo = self._mock_repo()
            MockRepo.return_value = repo

            config = BacktestConfig(
                symbol="AAPL",
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 12, 31),
                signals=["MACD_BULLISH"],  # Not RSI_OVERSOLD
            )
            engine.run(config)

            # add_trades should never have been called with a non-empty list.
            calls = repo.add_trades.call_args_list
            self.assertEqual(len(calls), 0)


if __name__ == "__main__":
    unittest.main()
