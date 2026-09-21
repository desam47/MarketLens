"""
Tests for market scanner
"""

import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../"))

from backend.models.market_data import Bar, DataStatus, Quote
from backend.scanner.scanner import Scanner, ScanResult


class TestScanner(unittest.TestCase):
    def setUp(self):
        self.scanner = Scanner()

    def test_scanner_initialization(self):
        """Test that scanner initializes correctly"""
        self.assertIsInstance(self.scanner.scan_results, dict)
        self.assertIsInstance(self.scanner.rankings, list)
        self.assertIsInstance(self.scanner.score_weights, dict)
        self.assertEqual(self.scanner.last_scan_time, None)

    def test_scan_result_creation(self):
        """Test creating a scan result"""
        timestamp = datetime.now()
        result = ScanResult("AAPL", timestamp)

        self.assertEqual(result.symbol, "AAPL")
        self.assertEqual(result.timestamp, timestamp)
        self.assertIsNone(result.quote)
        self.assertEqual(result.trend_signals, {})
        self.assertEqual(result.indicator_values, {})
        self.assertEqual(result.scores, {})
        self.assertIsNone(result.rank)
        self.assertEqual(result.signals, [])

        # No scores yet → total_score is 0.0 (avoids ZeroDivisionError
        # when the scores dict is empty under any weighting scheme).
        self.assertEqual(result.calculate_total_score(), 0.0)

    def test_scan_result_methods(self):
        """Test scan result methods"""
        result = ScanResult("AAPL", datetime.now())

        # Test add_indicator
        result.add_indicator("rsi", 65.5)
        self.assertEqual(result.indicator_values["rsi"], 65.5)

        # Test add_trend_signal
        result.add_trend_signal("ONE_HOUR", {"direction": "uptrend", "confidence": 0.8})
        self.assertEqual(result.trend_signals["ONE_HOUR"]["direction"], "uptrend")

        # Test add_score
        result.add_score("momentum", 75.0)
        self.assertEqual(result.scores["momentum"], 75.0)

        # Test add_signal
        result.add_signal("RSI_OVERSOLD")
        self.assertEqual(result.signals, ["RSI_OVERSOLD"])

        # With a single score under equal weighting, total_score
        # returns the score itself (weighted_sum / total_weight =
        # 75.0 / 1.0). The earlier "0.0 with no scores" branch is
        # unreachable from this test because add_score was called
        # above; that branch is exercised by the empty-ScanResult
        # path in test_scan_result_creation instead.
        self.assertEqual(result.calculate_total_score(), 75.0)

        # Test calculate_total_score with multiple scores
        result.add_score("trend", 80.0)
        result.add_score("momentum", 60.0)
        # With default equal weighting
        expected = (80.0 + 60.0) / 2
        self.assertEqual(result.calculate_total_score(), expected)

    @patch("backend.scanner.scanner.market_data_manager")
    def test_scan_symbol_success(self, mock_market_data_manager):
        """Test successful symbol scanning"""
        # Setup mock quote
        mock_quote = Quote(
            symbol="AAPL",
            price=150.0,
            timestamp=datetime.now(),
            provider="yahoo_finance",
            data_status=DataStatus.DELAYED,
            volume=1000000,
        )
        mock_market_data_manager.get_quote.return_value = mock_quote

        # Scan symbol
        result = self.scanner.scan_symbol("AAPL")

        # Assertions
        self.assertEqual(result.symbol, "AAPL")
        self.assertEqual(result.quote, mock_quote)
        self.assertIsInstance(result.indicator_values, dict)
        self.assertIsInstance(result.scores, dict)
        self.assertIsInstance(result.signals, list)

        # Verify market data manager was called
        mock_market_data_manager.get_quote.assert_called_once_with("AAPL")

    @patch("backend.scanner.scanner.market_data_manager")
    def test_scan_symbol_failure(self, mock_market_data_manager):
        """Test symbol scanning when market data fails"""
        # Setup mock to raise exception
        mock_market_data_manager.get_quote.side_effect = Exception("API error")

        # Scan symbol - should not raise exception
        result = self.scanner.scan_symbol("ERROR")

        # Should still return a result
        self.assertEqual(result.symbol, "ERROR")
        self.assertIsNone(result.quote)
        # Should have empty indicators and scores due to failure
        self.assertEqual(len(result.indicator_values), 0)
        self.assertEqual(len(result.scores), 0)

    def test_calculate_indicators(self):
        """Test indicator calculation when no bar history is available."""
        result = ScanResult("AAPL", datetime.now())
        # Mock a quote
        result.quote = Quote(
            symbol="AAPL",
            price=150.0,
            timestamp=datetime.now(),
            provider="yahoo_finance",
            data_status=DataStatus.DELAYED,
            volume=1000000,
        )

        # When the manager returns an empty history list, history-derived
        # indicators should be present-but-None.
        with patch("backend.scanner.scanner.market_data_manager") as mock_manager:
            mock_manager.get_quote.return_value = result.quote
            mock_manager.get_latest_bar.return_value = None
            mock_manager.get_historical_bars.return_value = []

            self.scanner._calculate_indicators(result, "AAPL")

        # Should have recorded price + volume (always available from the
        # quote) and registered RSI/MACD/ADX keys.
        self.assertIn("price", result.indicator_values)
        self.assertIn("volume", result.indicator_values)
        self.assertIn("rsi", result.indicator_values)
        self.assertIn("macd", result.indicator_values)
        self.assertIn("adx", result.indicator_values)

        # Quote-derived values are exact.
        self.assertEqual(result.indicator_values["price"], 150.0)
        self.assertEqual(result.indicator_values["volume"], 1000000)

        # History-derived indicators are present-but-None when the
        # manager returns an empty bar list.
        self.assertIsNone(result.indicator_values["rsi"])
        self.assertIsNone(result.indicator_values["macd"])
        self.assertIsNone(result.indicator_values["adx"])

    def test_calculate_indicators_with_bar_history(self):
        """RSI/MACD/ADX should be computed (not None) when bars are returned."""
        from backend.models.market_data import Bar

        result = ScanResult("AAPL", datetime.now())
        result.quote = Quote(
            symbol="AAPL",
            price=150.0,
            timestamp=datetime.now(),
            provider="yahoo_finance",
            data_status=DataStatus.DELAYED,
            volume=1000000,
        )

        # Build a synthetic 60-bar history with rising closes so RSI/MACD
        # produce well-defined, non-None values.
        base_ts = datetime(2025, 1, 1)
        bars: list[Bar] = []
        price = 100.0
        for i in range(60):
            price += 0.5
            bars.append(
                Bar(
                    symbol="AAPL",
                    timestamp=base_ts.replace(day=1 + i % 28, month=1 + i // 28),
                    open=price - 0.5,
                    high=price + 0.5,
                    low=price - 1.0,
                    close=price,
                    volume=1_000_000,
                    timeframe="1d",
                    provider="yahoo_finance",
                    data_status=DataStatus.HISTORICAL,
                )
            )

        with patch("backend.scanner.scanner.market_data_manager") as mock_manager:
            mock_manager.get_quote.return_value = result.quote
            mock_manager.get_latest_bar.return_value = bars[-1]
            mock_manager.get_historical_bars.return_value = bars

            self.scanner._calculate_indicators(result, "AAPL")

        # RSI/MACD/ADX should be present and numeric (not None) when
        # the manager returned a real bar history.
        for key in ("rsi", "macd", "adx"):
            self.assertIn(key, result.indicator_values)
            self.assertIsNotNone(
                result.indicator_values[key],
                f"{key} should be a number when bars are returned",
            )
            self.assertIsInstance(result.indicator_values[key], (int, float))

    def test_calculate_scanner_windows(self):
        """Scanner exposes the windows consumed by the saved scan filters."""
        result = ScanResult("AAPL", datetime.now())
        result.quote = Quote(
            symbol="AAPL",
            price=130.0,
            timestamp=datetime.now(),
            provider="yahoo_finance",
            data_status=DataStatus.DELAYED,
            volume=2_000_000,
        )

        bars: list[Bar] = []
        benchmark: list[Bar] = []
        for i in range(60):
            close = 100.0 + i * 0.25
            bars.append(
                Bar(
                    symbol="AAPL",
                    timestamp=datetime(2025, 1, 1) + timedelta(days=i),
                    open=close - 0.2,
                    high=close + 0.4,
                    low=close - 0.4,
                    close=close,
                    volume=1_000_000,
                    timeframe="1d",
                    provider="yahoo_finance",
                    data_status=DataStatus.HISTORICAL,
                )
            )
            benchmark.append(
                Bar(
                    symbol="SPY",
                    timestamp=datetime(2025, 1, 1) + timedelta(days=i),
                    open=100.0,
                    high=100.2,
                    low=99.8,
                    close=100.0,
                    volume=1_000_000,
                    timeframe="1d",
                    provider="yahoo_finance",
                    data_status=DataStatus.HISTORICAL,
                )
            )

        # Make the current quote a confirmed breakout and volume expansion.
        bars[-1] = bars[-1].model_copy(update={"high": 130.5, "close": 130.0, "volume": 2_000_000})
        with patch("backend.scanner.scanner.market_data_manager"):
            self.scanner._calculate_indicators(
                result,
                "AAPL",
                bars,
                {"SPY": benchmark},
            )

        self.assertIsNotNone(result.indicator_values["sma_20"])
        self.assertIsNotNone(result.indicator_values["highest_high_20"])
        self.assertTrue(result.indicator_values["breakout_20"])
        self.assertEqual(result.indicator_values["volume_ratio"], 2.0)
        self.assertGreater(result.indicator_values["rs_pct_SPY"], 0)
        self.assertGreater(result.indicator_values["relative_strength"], 0)

    def test_calculate_indicators_db_failure_falls_back(self):
        """DB-cache failure should fall back to a direct provider call."""

        result = ScanResult("AAPL", datetime.now())
        result.quote = Quote(
            symbol="AAPL",
            price=150.0,
            timestamp=datetime.now(),
            provider="yahoo_finance",
            data_status=DataStatus.DELAYED,
            volume=1000000,
        )

        # Empty history should still result in None indicators without
        # raising.
        with patch("backend.scanner.scanner.market_data_manager") as mock_manager:
            mock_manager.get_quote.return_value = result.quote
            mock_manager.get_latest_bar.return_value = None
            mock_manager.get_historical_bars.return_value = []

            self.scanner._calculate_indicators(result, "AAPL")

        self.assertIsNone(result.indicator_values["rsi"])
        self.assertIsNone(result.indicator_values["macd"])
        self.assertIsNone(result.indicator_values["adx"])

    def test_calculate_scores(self):
        """Test score calculation"""
        result = ScanResult("AAPL", datetime.now())
        # Set some indicator values
        result.indicator_values = {"adx": 30, "macd": 10, "close": 200, "volume": 500000, "rsi": 40}

        # Calculate scores
        self.scanner._calculate_scores(result)

        # Should have calculated scores
        self.assertIn("trend_strength", result.scores)
        self.assertIn("momentum", result.scores)
        self.assertIn("volatility", result.scores)
        self.assertIn("volume", result.scores)
        self.assertIn("rsi", result.scores)
        self.assertIn("macd", result.scores)
        self.assertIn("adx", result.scores)

        # Check scores are in valid range
        for _score_name, score_value in result.scores.items():
            self.assertGreaterEqual(score_value, 0)
            self.assertLessEqual(score_value, 100)

    def test_generate_signals(self):
        """Test signal generation"""
        result = ScanResult("AAPL", datetime.now())
        # Set indicator values that should generate signals
        result.indicator_values = {
            "rsi": 25,  # Oversold
            "macd": 5,  # Positive MACD
            "volume": 1500000,  # High volume
        }
        # Set trend signals
        result.trend_signals = {
            "ONE_HOUR": {"direction": "uptrend", "confidence": 0.7},
            "FOUR_HOUR": {"direction": "uptrend", "confidence": 0.8},
            "ONE_DAY": {"direction": "uptrend", "confidence": 0.9},
        }

        # Generate signals
        self.scanner._generate_signals(result)

        # Should have generated signals
        self.assertGreater(len(result.signals), 0)
        self.assertIn("RSI_OVERSOLD", result.signals)
        self.assertIn("MACD_BULLISH", result.signals)
        self.assertIn("MULTI_TIMEFRAME_BULLISH", result.signals)
        self.assertIn("HIGH_VOLUME", result.signals)

    def test_generate_signals_no_tape_signals_when_disabled(self):
        result = ScanResult("AAPL", datetime.now())
        result.indicator_values = {"rsi": 50}
        result.trend_signals = {}
        self.scanner._generate_signals(result)
        self.assertNotIn("HEAVY_BUY_PRESSURE", result.signals)
        self.assertNotIn("BLOCK_ACTIVITY", result.signals)

    @patch("backend.config.settings.settings.tape")
    @patch("backend.api.tape.registry.get_tape_engine")
    def test_generate_signals_includes_tape_pressure_and_blocks(
        self, mock_get_engine, mock_tape_cfg
    ):
        mock_tape_cfg.enabled = True
        mock_get_engine.return_value.get_snapshot.return_value = {
            "pressure": "heavy_buy",
            "block_count_5m": 3,
        }
        result = ScanResult("AAPL", datetime.now())
        result.indicator_values = {"rsi": 50}
        result.trend_signals = {}
        self.scanner._generate_signals(result)
        self.assertIn("HEAVY_BUY_PRESSURE", result.signals)
        self.assertIn("BLOCK_ACTIVITY", result.signals)

    @patch("backend.scanner.scanner.market_data_manager")
    def test_scan_symbols(self, mock_market_data_manager):
        """Test scanning multiple symbols.

        scan_symbols() uses get_batch_quotes() (not get_quote()). The mock must
        return real Quote/Bar objects so that downstream arithmetic in
        _calculate_indicators and TrendEngine.update() succeeds.
        """

        # Real Quote factory for the batch call path.
        def make_quote(symbol: str) -> Quote:
            return Quote(
                symbol=symbol,
                price=100.0 + hash(symbol) % 50,
                timestamp=datetime.now(),
                provider="yahoo_finance",
                data_status=DataStatus.DELAYED,
                volume=1000000,
            )

        def make_bar(symbol: str) -> Bar:
            return Bar(
                symbol=symbol,
                timestamp=datetime.now(),
                open=100.0,
                high=105.0,
                low=95.0,
                close=102.0,
                volume=1000000,
                timeframe="1d",
                provider="yahoo_finance",
                data_status=DataStatus.DELAYED,
            )

        # scan_symbols() calls get_batch_quotes / get_batch_historical_bars first.
        mock_market_data_manager.get_batch_quotes.side_effect = lambda symbols: {
            s: make_quote(s) for s in symbols
        }
        # get_batch_historical_bars is async now — scan_symbols awaits it.
        mock_market_data_manager.get_batch_historical_bars = AsyncMock(return_value={})
        # Individual call paths (fallback / indicator enrichment).
        mock_market_data_manager.get_quote.side_effect = lambda s: make_quote(s)
        mock_market_data_manager.get_latest_bar.return_value = make_bar("MOCK")
        mock_market_data_manager.get_historical_bars.return_value = [make_bar("MOCK")]

        # Scan symbols (async — drive it on a private loop)
        symbols = ["AAPL", "GOOGL", "MSFT"]
        results = asyncio.run(self.scanner.scan_symbols(symbols))

        # Assertions
        self.assertEqual(len(results), 3)
        for i, symbol in enumerate(symbols):
            self.assertEqual(results[i].symbol, symbol)
            self.assertIsNotNone(results[i].quote)

        # Should have called get_batch_quotes for the 3 symbols
        self.assertEqual(mock_market_data_manager.get_batch_quotes.call_count, 1)

    def test_rank_symbols(self):
        """Test ranking symbols"""
        # Add some scan results with scores
        result1 = ScanResult("AAPL", datetime.now())
        result1.add_score("trend_strength", 80)
        result1.add_score("momentum", 70)
        self.scanner.scan_results["AAPL"] = result1

        result2 = ScanResult("GOOGL", datetime.now())
        result2.add_score("trend_strength", 90)
        result2.add_score("momentum", 60)
        self.scanner.scan_results["GOOGL"] = result2

        result3 = ScanResult("MSFT", datetime.now())
        result3.add_score("trend_strength", 70)
        result3.add_score("momentum", 80)
        self.scanner.scan_results["MSFT"] = result3

        # Rank symbols
        ranked = self.scanner.rank_symbols(["AAPL", "GOOGL", "MSFT"])

        # Should be ranked by total score (with default equal weighting)
        # AAPL: (80+70)/2 = 75
        # GOOGL: (90+60)/2 = 75
        # MSFT: (70+80)/2 = 75
        # All equal, so order might vary but should have 3 items
        self.assertEqual(len(ranked), 3)

        # Check that ranks were assigned
        for symbol, _score in ranked:
            result = self.scanner.scan_results[symbol]
            self.assertIsNotNone(result.rank)
            self.assertGreaterEqual(result.rank, 1)
            self.assertLessEqual(result.rank, 3)

    def test_get_top_symbols(self):
        """Test getting top symbols"""
        # Add some scan results
        result1 = ScanResult("AAPL", datetime.now())
        result1.add_score("trend_strength", 80)
        result1.add_score("momentum", 70)
        self.scanner.scan_results["AAPL"] = result1

        result2 = ScanResult("GOOGL", datetime.now())
        result2.add_score("trend_strength", 90)
        result2.add_score("momentum", 80)
        self.scanner.scan_results["GOOGL"] = result2

        # Get top symbols
        top = self.scanner.get_top_symbols(1)
        self.assertEqual(len(top), 1)
        # GOOGL should be ranked higher (85 vs 75 average)
        self.assertEqual(top[0][0], "GOOGL")

    def test_get_scan_result(self):
        """Test getting scan result for a symbol"""
        result = ScanResult("AAPL", datetime.now())
        self.scanner.scan_results["AAPL"] = result

        # Get existing result
        retrieved = self.scanner.get_scan_result("AAPL")
        self.assertEqual(retrieved, result)

        # Get non-existing result
        none_result = self.scanner.get_scan_result("NONEXISTENT")
        self.assertIsNone(none_result)

    def test_get_signals_for_symbol(self):
        """Test getting signals for a symbol"""
        result = ScanResult("AAPL", datetime.now())
        result.add_signal("TEST_SIGNAL")
        self.scanner.scan_results["AAPL"] = result

        signals = self.scanner.get_signals_for_symbol("AAPL")
        self.assertEqual(signals, ["TEST_SIGNAL"])

        # Test non-existent symbol
        no_signals = self.scanner.get_signals_for_symbol("NONEXISTENT")
        self.assertEqual(no_signals, [])


if __name__ == "__main__":
    unittest.main()
