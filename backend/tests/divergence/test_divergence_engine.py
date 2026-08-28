"""Tests for DivergenceEngine."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from backend.divergence.divergence_engine import (
    DivergenceDirection,
    DivergenceEngine,
    DivergenceType,
)


def _bars(n: int, base: float = 100.0) -> tuple:
    """Generate n synthetic OHLCV bars that are all slightly rising."""
    closes = [base + i * 0.5 for i in range(n)]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    volumes = [1_000_000 + i * 1000 for i in range(n)]
    return highs, lows, closes, volumes


def _bearish_rsi_setup() -> tuple:
    """Price makes higher-high; RSI makes lower-high → bearish RSI divergence.

    With pivot_lookback=2 the engine needs at least 2 bars of lower highs
    on each side of a confirmed swing high. The price series here oscillates
    between uplegs and pullbacks to create distinct local peaks.
    """
    # Layout: [dip, peak1, dip, peak2(=higher), dip, ..., peak3(=same)] + tails
    # highs[i] determines swing highs; peaks must be separated by >2 bars.
    # Pattern: valley-peak-valley-peak-valley...
    # Peaks at indices 3, 9  (105.0 → 112.0 = higher)
    highs = []
    lows = []
    closes = []
    n = 60
    for i in range(n):
        # Oscillate so we get clear swing highs and lows
        phase = i % 10
        if phase <= 3:
            # Rising phase
            c = 100.0 + phase * 1.5
        else:
            # Falling phase
            c = 100.0 + (10 - phase) * 1.5
        # Pin two clear peaks at index 3 (105.0) and 9 (112.0)
        if i == 3:
            c = 105.0
        elif i == 9:
            c = 112.0
        closes.append(c)
        highs.append(c + 1.5)
        lows.append(c - 1.5)

    # RSI: rises with first peak (to 70), falls with second peak (to 50)
    # This creates the lower-high in RSI that signals bearish divergence
    rsi = []
    for i in range(n):
        if i <= 3:
            rsi.append(40.0 + i * 7.5)   # 40 → 62.5 at peak1
        elif i <= 9:
            rsi.append(62.5 - (i - 3) * 2.5)  # 62.5 → 42.5 at peak2
        else:
            rsi.append(45.0)
    return highs, lows, closes, rsi


def _bullish_rsi_setup() -> tuple:
    """Price makes lower-low; RSI makes higher-low → bullish RSI divergence.

    With pivot_lookback=2 the engine needs at least 2 bars of higher lows
    on each side of a confirmed swing low. The price series oscillates
    between troughs that create two distinct local lows.
    """
    highs = []
    lows = []
    closes = []
    n = 60
    for i in range(n):
        phase = i % 10
        if phase <= 3:
            c = 120.0 - phase * 2.0     # falling phase
        else:
            c = 120.0 - (10 - phase) * 2.0  # rising phase
        # Two troughs at index 4 (105.0) and 10 (98.0 = lower)
        if i == 4:
            c = 105.0
        elif i == 10:
            c = 98.0
        closes.append(c)
        highs.append(c + 1.5)
        lows.append(c - 1.5)

    # RSI: falls with first trough (to 30), rises with second trough (to 55)
    rsi = []
    for i in range(n):
        if i <= 4:
            rsi.append(60.0 - i * 6.0)     # 60 → 36 at trough1
        elif i <= 10:
            rsi.append(36.0 + (i - 4) * 3.0)  # 36 → 54 at trough2 (higher)
        else:
            rsi.append(55.0)
    return highs, lows, closes, rsi


class TestDivergenceEngine(unittest.TestCase):

    def test_initialization_defaults(self):
        engine = DivergenceEngine()
        self.assertEqual(engine.pivot_lookback, 2)
        self.assertEqual(engine.max_pivots_apart, 60)
        self.assertEqual(engine.min_price_delta_pct, 0.5)

    def test_validation(self):
        with self.assertRaises(ValueError):
            DivergenceEngine(pivot_lookback=0)
        with self.assertRaises(ValueError):
            DivergenceEngine(max_pivots_apart=1)

    def test_insufficient_data(self):
        engine = DivergenceEngine()
        highs, lows, closes, _ = _bars(3)
        result = engine.detect(highs, lows, closes)
        self.assertEqual(result, [])

    def test_no_divergence_flat_series(self):
        """A flat rising series produces no divergence."""
        engine = DivergenceEngine(pivot_lookback=2)
        highs, lows, closes, volumes = _bars(50)
        result = engine.detect(highs, lows, closes, volumes=volumes)
        # Flat-ish series: price rises consistently, no pivot-based divergences
        self.assertEqual(result, [])

    def test_bearish_rsi_divergence_detected(self):
        engine = DivergenceEngine(pivot_lookback=2)
        highs, lows, closes, rsi = _bearish_rsi_setup()
        result = engine.detect(highs, lows, closes, rsi=rsi)
        types = [d.type for d in result]
        self.assertIn(DivergenceType.BEARISH_RSI, types)
        # Confirm direction
        bearishes = [d for d in result if d.type == DivergenceType.BEARISH_RSI]
        self.assertTrue(all(d.direction == DivergenceDirection.BEARISH for d in bearishes))

    def test_bullish_rsi_divergence_detected(self):
        engine = DivergenceEngine(pivot_lookback=2)
        highs, lows, closes, rsi = _bullish_rsi_setup()
        result = engine.detect(highs, lows, closes, rsi=rsi)
        types = [d.type for d in result]
        self.assertIn(DivergenceType.BULLISH_RSI, types)
        bullishes = [d for d in result if d.type == DivergenceType.BULLISH_RSI]
        self.assertTrue(all(d.direction == DivergenceDirection.BULLISH for d in bullishes))

    def test_macd_divergence_requires_macd_input(self):
        """Without macd input, no macd divergences fire."""
        engine = DivergenceEngine(pivot_lookback=2)
        highs, lows, closes, rsi = _bearish_rsi_setup()
        result = engine.detect(highs, lows, closes, rsi=rsi)
        macd_types = [d for d in result if "macd" in d.type.value]
        self.assertEqual(macd_types, [])

    def test_volume_divergence_requires_volume_input(self):
        """Without volume input, no volume divergences fire."""
        engine = DivergenceEngine(pivot_lookback=2)
        highs, lows, closes, rsi = _bearish_rsi_setup()
        result = engine.detect(highs, lows, closes, rsi=rsi)
        vol_types = [d for d in result if "volume" in d.type.value]
        self.assertEqual(vol_types, [])

    def test_strength_in_range(self):
        engine = DivergenceEngine(pivot_lookback=2)
        highs, lows, closes, rsi = _bearish_rsi_setup()
        result = engine.detect(highs, lows, closes, rsi=rsi)
        for d in result:
            self.assertGreaterEqual(d.strength, 0.0)
            self.assertLessEqual(d.strength, 1.0)

    def test_symbol_and_timeframe_stamped(self):
        engine = DivergenceEngine(pivot_lookback=2)
        highs, lows, closes, rsi = _bearish_rsi_setup()
        result = engine.detect(highs, lows, closes, rsi=rsi, symbol="AAPL", timeframe="1h")
        if result:
            self.assertEqual(result[0].symbol, "AAPL")
            self.assertEqual(result[0].timeframe, "1h")

    def test_to_dict(self):
        engine = DivergenceEngine(pivot_lookback=2)
        highs, lows, closes, rsi = _bearish_rsi_setup()
        result = engine.detect(highs, lows, closes, rsi=rsi)
        if result:
            d = result[0].to_dict()
            self.assertIn("type", d)
            self.assertIn("pivot_a_price", d)
            self.assertIn("strength", d)


if __name__ == "__main__":
    unittest.main()
