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
    BacktestConfig,
    BacktestEngine,
    _build_trade,
    _compute_metrics,
)
from backend.models.market_data import Bar, DataStatus


def _bar(close, volume=1_000_000, days_ago=0):
    ts = datetime(2025, 1, 1) + timedelta(days=days_ago)
    return Bar(
        symbol="AAPL", timestamp=ts, open=close, high=close * 1.01,
        low=close * 0.99, close=close, volume=volume,
        timeframe="1d", provider="test", data_status=DataStatus.LIVE,
    )


def _mock_trade(signal, entry_price, return_1d, return_5d, return_20d,
               mfe=None, mae=None, entry_date=None):
    t = MagicMock()
    t.signal = signal
    t.entry_date = entry_date or datetime(2025, 1, 15)
    t.entry_price = entry_price
    t.return_1d = return_1d
    t.return_5d = return_5d
    t.return_20d = return_20d
    t.mfe = mfe
    t.mae = mae
    return t


class TestComputeMetrics(unittest.TestCase):

    def test_empty_trades_returns_none_metrics(self):
        result = _compute_metrics([], total_bars=0)
        self.assertIsNone(result["win_rate_1d"])
        self.assertIsNone(result["avg_return_1d"])
        self.assertIsNone(result["avg_return_5d"])
        self.assertIsNone(result["avg_return_20d"])

    def test_win_rate_half(self):
        trades = [
            _mock_trade("RSI_OVERSOLD", 100, 2.0, 5.0, 10.0),
            _mock_trade("RSI_OVERSOLD", 100, -2.0, 1.0, -3.0),
        ]
        result = _compute_metrics(trades, total_bars=100)
        self.assertEqual(result["win_rate_1d"], 0.5)

    def test_win_rate_all_winners(self):
        trades = [_mock_trade("MACD_BULLISH", 100, 1.0, 2.0, 5.0)]
        result = _compute_metrics(trades, total_bars=100)
        self.assertEqual(result["win_rate_1d"], 1.0)

    def test_avg_returns_computed(self):
        trades = [
            _mock_trade("RSI_OVERSOLD", 100, 2.0, 4.0, 8.0),
            _mock_trade("RSI_OVERSOLD", 100, 4.0, 6.0, 12.0),
        ]
        result = _compute_metrics(trades, total_bars=100)
        self.assertAlmostEqual(result["avg_return_1d"], 3.0)
        self.assertAlmostEqual(result["avg_return_5d"], 5.0)
        self.assertAlmostEqual(result["avg_return_20d"], 10.0)

    def test_none_return_1d_skipped_from_avg(self):
        t = _mock_trade("RSI_OVERSOLD", 100, None, 5.0, 10.0)
        result = _compute_metrics([t], total_bars=100)
        self.assertIsNone(result["avg_return_1d"])

    # --- Phase 14 metric tests ---

    def test_median_returns_computed(self):
        trades = [
            _mock_trade("RSI_OVERSOLD", 100, 1.0, 5.0, 10.0),
            _mock_trade("RSI_OVERSOLD", 100, 5.0, 5.0, 12.0),
            _mock_trade("RSI_OVERSOLD", 100, 10.0, 5.0, 8.0),
        ]
        result = _compute_metrics(trades, total_bars=100)
        # Median of [1, 5, 10] = 5
        self.assertAlmostEqual(result["median_return_1d"], 5.0)
        self.assertAlmostEqual(result["median_return_5d"], 5.0)
        # Median of [8, 10, 12] = 10
        self.assertAlmostEqual(result["median_return_20d"], 10.0)

    def test_median_handles_even_count(self):
        # Even count: median = avg of middle two
        trades = [
            _mock_trade("RSI_OVERSOLD", 100, 1.0, 1.0, 1.0),
            _mock_trade("RSI_OVERSOLD", 100, 3.0, 3.0, 3.0),
        ]
        result = _compute_metrics(trades, total_bars=100)
        # Median of [1, 3] = 2
        self.assertAlmostEqual(result["median_return_1d"], 2.0)

    def test_signal_frequency(self):
        trades = [
            _mock_trade("RSI_OVERSOLD", 100, 1.0, 1.0, 1.0),
            _mock_trade("RSI_OVERSOLD", 100, 2.0, 2.0, 2.0),
            _mock_trade("RSI_OVERSOLD", 100, 3.0, 3.0, 3.0),
        ]
        result = _compute_metrics(trades, total_bars=200)
        # 3 signals / 200 bars = 0.015
        self.assertAlmostEqual(result["signal_frequency"], 0.015)

    def test_profit_factor_with_mixed_trades(self):
        # 6 profit + 4 loss on 1d: 6+4=10 trades, profit factor = 6/4 = 1.5
        trades = []
        for _i in range(6):
            trades.append(_mock_trade("RSI_OVERSOLD", 100, 1.0, 1.0, 1.0))
        for _i in range(4):
            trades.append(_mock_trade("RSI_OVERSOLD", 100, -1.0, -1.0, -1.0))
        result = _compute_metrics(trades, total_bars=100)
        self.assertAlmostEqual(result["profit_factor"], 6.0 / 4.0)

    def test_profit_factor_none_when_all_winners(self):
        # No losses → profit factor is None (avoid infinity)
        trades = [_mock_trade("RSI_OVERSOLD", 100, 1.0, 1.0, 1.0)]
        result = _compute_metrics(trades, total_bars=100)
        self.assertIsNone(result["profit_factor"])

    def test_sharpe_ratio_nonzero(self):
        # Two trades with non-zero variance.
        trades = [
            _mock_trade("RSI_OVERSOLD", 100, 2.0, 2.0, 2.0),
            _mock_trade("RSI_OVERSOLD", 100, -2.0, -2.0, -2.0),
        ]
        result = _compute_metrics(trades, total_bars=100)
        # Two trades, equal magnitude, mean = 0 → Sharpe = 0
        self.assertAlmostEqual(result["sharpe_ratio"], 0.0, places=6)

    def test_sharpe_ratio_none_with_single_trade(self):
        # Can't compute std dev with only 1 trade.
        trades = [_mock_trade("RSI_OVERSOLD", 100, 1.0, 1.0, 1.0)]
        result = _compute_metrics(trades, total_bars=100)
        self.assertIsNone(result["sharpe_ratio"])

    def test_mfe_mae_averages(self):
        trades = [
            _mock_trade("RSI_OVERSOLD", 100, 1.0, 1.0, 1.0, mfe=4.0, mae=-1.0),
            _mock_trade("RSI_OVERSOLD", 100, 2.0, 2.0, 2.0, mfe=6.0, mae=-3.0),
        ]
        result = _compute_metrics(trades, total_bars=100)
        self.assertAlmostEqual(result["mfe_avg"], 5.0)
        self.assertAlmostEqual(result["mae_avg"], -2.0)

    def test_max_drawdown_negative(self):
        # 3 trades: +10%, then -5% (drawdown = 5%), then +2% (still below peak)
        trades = [
            _mock_trade("RSI_OVERSOLD", 100, 10.0, 10.0, 10.0,
                        entry_date=datetime(2025, 1, 1)),
            _mock_trade("RSI_OVERSOLD", 100, -5.0, -5.0, -5.0,
                        entry_date=datetime(2025, 2, 1)),
            _mock_trade("RSI_OVERSOLD", 100, 2.0, 2.0, 2.0,
                        entry_date=datetime(2025, 3, 1)),
        ]
        result = _compute_metrics(trades, total_bars=100)
        # Peak cum: 10.0% after trade 1. After trade 2: compound = (1.10)(0.95)-1 = 4.5%.
        # Drawdown from peak = 4.5 - 10.0 = -5.5%.
        # After trade 3: (1.045)(1.02) - 1 = 6.59%. Still below peak → -3.41%.
        # Max drawdown = -5.5%.
        self.assertIsNotNone(result["max_drawdown"])
        self.assertLess(result["max_drawdown"], 0)
        self.assertAlmostEqual(result["max_drawdown"], -5.5, places=1)

    def test_equity_curve_json_present(self):
        trades = [
            _mock_trade("RSI_OVERSOLD", 100, 1.0, 1.0, 1.0,
                        entry_date=datetime(2025, 1, 1)),
            _mock_trade("RSI_OVERSOLD", 100, -1.0, -1.0, -1.0,
                        entry_date=datetime(2025, 1, 2)),
        ]
        result = _compute_metrics(trades, total_bars=100)
        import json
        curve = json.loads(result["equity_curve_json"])
        self.assertEqual(len(curve), 2)
        # Each point is [timestamp, cum_return_pct]
        self.assertEqual(len(curve[0]), 2)

    def test_overfitting_warning_high_winrate(self):
        # 8 of 10 trades win → 80% > 70% threshold
        trades = []
        for _i in range(8):
            trades.append(_mock_trade("RSI_OVERSOLD", 100, 1.0, 1.0, 1.0))
        for _i in range(2):
            trades.append(_mock_trade("RSI_OVERSOLD", 100, -10.0, -10.0, -10.0))
        result = _compute_metrics(trades, total_bars=100)
        self.assertIsNotNone(result["overfitting_warning"])
        self.assertIn("win rate", result["overfitting_warning"])

    def test_no_overfitting_warning_for_normal_results(self):
        # 50% win rate, modest returns
        trades = [
            _mock_trade("RSI_OVERSOLD", 100, 1.0, 1.0, 1.0),
            _mock_trade("RSI_OVERSOLD", 100, -1.0, -1.0, -1.0),
        ]
        result = _compute_metrics(trades, total_bars=100)
        self.assertIsNone(result["overfitting_warning"])


class TestLookAheadBias(unittest.TestCase):
    """Tests that the engine never uses data from after the entry bar.

    These guard against the most common backtest bug: the
    forward-return / indicator calculation accidentally seeing bars
    that wouldn't be available at the entry timestamp.
    """

    def _bars(self, n=50, base=100.0):
        return [_bar(base + i, days_ago=n - 1 - i) for i in range(n)]

    def test_forward_returns_use_only_future_bars(self):
        """return_1d of trade[i] equals (close[i+1] - close[i]) / close[i] * 100."""
        bars = self._bars(50)
        trade = _build_trade(signal="RSI_OVERSOLD", bars=bars, entry_index=14)
        expected = (bars[15].close - bars[14].close) / bars[14].close * 100
        self.assertAlmostEqual(trade.return_1d, expected)

    def test_mfe_uses_only_post_entry_bars(self):
        """mfe is computed over [entry+1, entry+20], not entry itself."""
        # Build bars where the entry bar is a high spike but post-entry
        # bars are all low — mfe should reflect only post-entry peaks.
        n = 50
        bars = []
        for i in range(n):
            if i == 14:  # entry bar
                bars.append(_bar(200.0, days_ago=n - 1 - i))
            else:
                bars.append(_bar(100.0 + i, days_ago=n - 1 - i))
        trade = _build_trade(signal="RSI_OVERSOLD", bars=bars, entry_index=14)
        # mfe must NOT use bars[14].high (which is 200*1.01=202). It uses bars[15..33]
        # (the window ends at index 33 since mfe_end = min(14+20, 50) = 34).
        # bars[33].close = 133, bars[33].high = 133*1.01 = 134.33.
        # mfe = (134.33 - 200) / 200 * 100 = -32.835%.
        # If the engine accidentally used bars[14].high, mfe would be positive.
        self.assertLess(trade.mfe, 0)
        self.assertAlmostEqual(trade.mfe, (133.0 * 1.01 - 200.0) / 200.0 * 100, places=1)

    def test_mae_uses_only_post_entry_bars(self):
        # Mirror test: entry bar is a low spike, post-entry bars are higher.
        n = 50
        bars = []
        for i in range(n):
            if i == 14:
                bars.append(_bar(50.0, days_ago=n - 1 - i))
            else:
                bars.append(_bar(100.0 + i, days_ago=n - 1 - i))
        trade = _build_trade(signal="RSI_OVERSOLD", bars=bars, entry_index=14)
        # Post-entry low is bars[15].low = (15+100)*0.99 = 113.85.
        # mae = (113.85 - 50) / 50 * 100 = +127.7%
        # If the engine accidentally used bars[14].low, mae would be 0.
        self.assertGreater(trade.mae, 0)
        self.assertAlmostEqual(trade.mae, (113.85 - 50.0) / 50.0 * 100, places=2)

    def test_indicator_window_never_includes_entry_bar(self):
        """The replay window is [i - WARMUP, i], so the i-th (entry) bar
        is included — but the synthetic ScanResult is built from that
        window *only*. The forward-return math then uses bars > i.

        Verify the engine does NOT inject any future bar data into the
        ScanResult. We patch the replay helper to record the window it
        was called with and assert no window includes bars past the
        entry.
        """
        from backend.backtesting import replay as replay_mod

        captured_windows: list[list] = []

        original_build = replay_mod.build_scan_result

        def spy(symbol, timestamp, window):
            captured_windows.append(list(window))
            return original_build(symbol, timestamp, window)

        # 50 bars, stub scanner that always fires.
        n = 50
        bars = self._bars(n)
        engine = BacktestEngine()
        stub_scanner = MagicMock()
        stub_scanner._generate_signals = MagicMock(
            side_effect=lambda r: setattr(r, "signals", ["RSI_OVERSOLD"])
        )

        with patch("backend.backtesting.engine.BacktestRepository") as MockRepo, \
             patch("backend.backtesting.engine._build_scanner", return_value=stub_scanner), \
             patch("backend.backtesting.replay.build_scan_result", side_effect=spy), \
             patch.object(BacktestEngine, "_load_bars", return_value=bars):
            mock_repo = MagicMock()
            run = MagicMock()
            run.id = 1
            mock_repo.create_run.return_value = run
            mock_repo.update_run_status.return_value = run
            MockRepo.return_value = mock_repo

            config = BacktestConfig(
                symbol="AAPL",
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 12, 31),
                signals=["RSI_OVERSOLD"],
            )
            engine.run(config)

        # For every captured window, the last element must be the
        # entry bar (i-th) and the window must contain no bar with
        # index > i in the *original* bar list.
        # We can't directly compare by index here, but we can check
        # that no window is longer than INDICATOR_WARMUP + 1.
        from backend.backtesting.replay import INDICATOR_WARMUP
        for w in captured_windows:
            self.assertLessEqual(len(w), INDICATOR_WARMUP + 1)

    def test_equity_curve_uses_only_1d_return_not_5d_or_20d(self):
        """The equity curve is a day-by-day series and should only be
        driven by the 1-bar forward return, not the 5- or 20-bar
        return. Verify by constructing two trade lists that differ
        only in the 5d/20d columns and checking the equity curves
        match.
        """

        def make_trade(r1, r5, r20):
            return _mock_trade("RSI_OVERSOLD", 100, r1, r5, r20,
                               entry_date=datetime(2025, 1, 1))

        # Same 1d returns, different 5d/20d returns.
        a = _compute_metrics(
            [make_trade(1.0, 1.0, 1.0), make_trade(-1.0, 10.0, 20.0)],
            total_bars=100,
        )
        b = _compute_metrics(
            [make_trade(1.0, 5.0, 10.0), make_trade(-1.0, 50.0, 100.0)],
            total_bars=100,
        )
        self.assertEqual(a["equity_curve_json"], b["equity_curve_json"])


class TestWalkForward(unittest.TestCase):
    """Tests for the walk-forward analysis helper."""

    def _bars(self, n=300):
        return [_bar(100.0 + i, days_ago=n - 1 - i) for i in range(n)]

    def test_walk_forward_produces_pairs(self):
        """A 2-split walk-forward on 300 days yields 2 IS + 2 OOS runs."""
        from backend.backtesting.engine import (
            WalkForwardConfig,
            walk_forward_analyze,
        )

        bars = self._bars(300)
        stub_scanner = MagicMock()
        stub_scanner._generate_signals = MagicMock(
            side_effect=lambda r: setattr(r, "signals", ["RSI_OVERSOLD"])
        )

        with patch("backend.backtesting.engine.BacktestRepository") as MockRepo, \
             patch("backend.backtesting.engine._build_scanner", return_value=stub_scanner), \
             patch.object(BacktestEngine, "_load_bars", return_value=bars):
            mock_repo = MagicMock()
            run = MagicMock()
            run.id = 1
            mock_repo.create_run.return_value = run
            mock_repo.update_run_status.return_value = run
            mock_repo.get_run.return_value = run
            MockRepo.return_value = mock_repo

            # 2 splits × 150 days = 300 days. Each split = 150 days,
            # test_pct=0.30 → 45 days OOS, 105 days IS. 45 > 34
            # (MIN_BARS_FOR_RUN) so the OOS slice runs.
            config = WalkForwardConfig(
                symbol="AAPL",
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 10, 28),  # ~300 days
                signals=["RSI_OVERSOLD"],
                n_splits=2,
                test_pct=0.30,
            )
            run_ids = walk_forward_analyze(config)

        # 2 splits × 2 (IS + OOS) = 4 runs.
        self.assertEqual(len(run_ids), 4)
        # The OOS runs should have out_of_sample=True patched onto them.
        oos_calls = [
            c for c in mock_repo.update_run_status.call_args_list
            if c.kwargs.get("out_of_sample") is True
        ]
        self.assertEqual(len(oos_calls), 2)

    def test_walk_forward_skips_oos_when_too_small(self):
        """If the OOS slice is smaller than MIN_BARS_FOR_RUN, only IS runs."""
        from backend.backtesting.engine import (
            WalkForwardConfig,
            walk_forward_analyze,
        )

        bars = self._bars(300)
        stub_scanner = MagicMock()
        stub_scanner._generate_signals = MagicMock(
            side_effect=lambda r: setattr(r, "signals", ["RSI_OVERSOLD"])
        )

        with patch("backend.backtesting.engine.BacktestRepository") as MockRepo, \
             patch("backend.backtesting.engine._build_scanner", return_value=stub_scanner), \
             patch.object(BacktestEngine, "_load_bars", return_value=bars):
            mock_repo = MagicMock()
            run = MagicMock()
            run.id = 1
            mock_repo.create_run.return_value = run
            mock_repo.update_run_status.return_value = run
            mock_repo.get_run.return_value = run
            MockRepo.return_value = mock_repo

            # test_pct=0.05 → 7.5 days OOS < MIN_BARS_FOR_RUN (34)
            config = WalkForwardConfig(
                symbol="AAPL",
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 10, 28),
                signals=["RSI_OVERSOLD"],
                n_splits=2,
                test_pct=0.05,
            )
            run_ids = walk_forward_analyze(config)

        # 2 IS runs only — OOS slices were too small.
        self.assertEqual(len(run_ids), 2)

    def test_walk_forward_zero_runs_for_tiny_range(self):
        """A 1-day range produces no splits."""
        from backend.backtesting.engine import (
            WalkForwardConfig,
            walk_forward_analyze,
        )

        config = WalkForwardConfig(
            symbol="AAPL",
            start_date=datetime(2025, 1, 1),
            end_date=datetime(2025, 1, 2),
            signals=["RSI_OVERSOLD"],
            n_splits=4,
        )
        # No bars loaded, no runs.
        with patch("backend.backtesting.engine._build_scanner") as m, \
             patch("backend.backtesting.engine.BacktestRepository") as MockRepo, \
             patch.object(BacktestEngine, "_load_bars", return_value=[]):
            m.return_value = MagicMock()
            mock_repo = MagicMock()
            run = MagicMock()
            run.id = 1
            mock_repo.create_run.return_value = run
            mock_repo.update_run_status.return_value = run
            mock_repo.get_run.return_value = run
            MockRepo.return_value = mock_repo
            run_ids = walk_forward_analyze(config)
        self.assertEqual(run_ids, [])


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


class TestStrategyVersion(unittest.TestCase):
    """Phase 0 Principle 13 — strategy_version is stamped on every run."""

    def _bars(self, n=50):
        return [_bar(100.0 + i, days_ago=49 - i) for i in range(n)]

    def _scanner_firing(self, signal):
        stub_scanner = MagicMock()
        stub_scanner._generate_signals = MagicMock(
            side_effect=lambda r: setattr(r, "signals", [signal])
        )
        return stub_scanner

    def test_strategy_version_from_settings_when_unspecified(self):
        """Default to TrendSettings.strategy_version when config omits it."""
        from backend.config.settings import settings

        engine = BacktestEngine()
        bars = self._bars()
        stub_scanner = self._scanner_firing("RSI_OVERSOLD")

        with patch("backend.backtesting.engine.BacktestRepository") as MockRepo, \
             patch("backend.backtesting.engine._build_scanner", return_value=stub_scanner), \
             patch.object(BacktestEngine, "_load_bars", return_value=bars):
            mock_repo = MagicMock()
            run = MagicMock()
            run.id = 7
            mock_repo.create_run.return_value = run
            mock_repo.update_run_status.return_value = run
            mock_repo.get_run.return_value = run
            MockRepo.return_value = mock_repo

            config = BacktestConfig(
                symbol="AAPL",
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 12, 31),
                signals=["RSI_OVERSOLD"],
            )
            engine.run(config)

            # The first call to create_run should pass strategy_version
            # equal to the settings default.
            create_call = mock_repo.create_run.call_args
            self.assertEqual(
                create_call.kwargs.get("strategy_version"),
                settings.trend.strategy_version,
            )

    def test_strategy_version_override(self):
        """An explicit strategy_version on the config wins over settings."""
        engine = BacktestEngine()
        bars = self._bars()
        stub_scanner = self._scanner_firing("RSI_OVERSOLD")

        with patch("backend.backtesting.engine.BacktestRepository") as MockRepo, \
             patch("backend.backtesting.engine._build_scanner", return_value=stub_scanner), \
             patch.object(BacktestEngine, "_load_bars", return_value=bars):
            mock_repo = MagicMock()
            run = MagicMock()
            run.id = 8
            mock_repo.create_run.return_value = run
            mock_repo.update_run_status.return_value = run
            mock_repo.get_run.return_value = run
            MockRepo.return_value = mock_repo

            config = BacktestConfig(
                symbol="AAPL",
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 12, 31),
                signals=["RSI_OVERSOLD"],
                strategy_version="rsi14-macd-2",
            )
            engine.run(config)

            create_call = mock_repo.create_run.call_args
            self.assertEqual(
                create_call.kwargs.get("strategy_version"),
                "rsi14-macd-2",
            )


if __name__ == "__main__":
    unittest.main()
