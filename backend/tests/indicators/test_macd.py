"""
Tests for MACD indicator
"""

import math
import unittest

from backend.indicators.macd import MACDIndicator


class TestMACDIndicator(unittest.TestCase):
    def setUp(self):
        self.indicator = MACDIndicator(fast=12, slow=26, signal=9)

    def test_macd_calculation(self):
        """Test MACD calculation with known values"""
        # Create test data with enough points for MACD calculation
        data = []
        for i in range(35):  # Need enough data for slow EMA (26) + signal (9)
            data.append(
                {
                    "close": 100 + i * 0.5  # Gradually increasing price
                }
            )

        # Calculate MACD
        macd_values = self.indicator.calculate(data)

        # Should have calculated values
        self.assertGreater(len(macd_values), 0)

        # All values should be floats
        for value in macd_values:
            self.assertIsInstance(value, float)

    def test_macd_update(self):
        """Test MACD updating with new data points"""
        # Build up enough data via update() to get a MACD histogram value.
        # Need at least slow(26) + signal(9) = 35 updates before a histogram appears.
        for i in range(36):
            result = self.indicator.update({"close": 100 + i * 0.5})

        # After enough data, update() should return a float histogram value
        self.assertIsNotNone(result)
        self.assertIsInstance(result, float)

        # Update with another point to confirm continued functionality
        next_result = self.indicator.update({"close": 120.0})
        self.assertIsNotNone(next_result)
        self.assertIsInstance(next_result, float)

    def test_macd_insufficient_data(self):
        """Test MACD with insufficient data"""
        # Not enough data for MACD calculation
        data = [{"close": 100}, {"close": 101}, {"close": 102}]

        # Calculate MACD
        macd_values = self.indicator.calculate(data)

        # Should return empty list
        self.assertEqual(len(macd_values), 0)

    def test_signal_line_uses_ema_after_single_seed(self):
        """The signal-line warmup must fire the SMA seed exactly once (the
        bar macd_line first reaches length `signal`), then switch to the
        standard EMA formula for every subsequent bar.

        Regression test: `len(self.signal_line) < self.signal - 1`
        stayed true for `signal - 1` consecutive bars (8, for signal=9),
        during which update() kept recomputing a sliding-window
        SMA(signal) of macd_line instead of applying the EMA formula --
        contradicting this indicator's own docstring and diverging from
        the offline calculate() path (a real EMAIndicator, seeded once).
        """
        price = 100.0
        data = []
        for i in range(60):
            osc = math.sin(i * 0.5) * 2.0
            price += 0.3
            data.append({"close": price + osc})

        ind = MACDIndicator(fast=12, slow=26, signal=9)
        seed_value = None
        second_value = None
        last_macd = None
        for bar in data:
            ind.update(bar)
            if seed_value is None and len(ind.signal_line) == 1:
                seed_value = ind.signal_line[0]
            elif second_value is None and len(ind.signal_line) == 2:
                second_value = ind.signal_line[-1]
                last_macd = ind.macd_line[-1]
                break

        self.assertIsNotNone(seed_value)
        self.assertIsNotNone(second_value)
        self.assertIsNotNone(last_macd)

        k = 2 / (ind.signal + 1)
        expected_ema = last_macd * k + seed_value * (1 - k)
        self.assertAlmostEqual(second_value, expected_ema, places=9)

        # Rule out the old bug directly: the second signal value must NOT
        # be the sliding-window SMA of the last `signal` MACD values.
        buggy_sma = sum(ind.macd_line[-ind.signal :]) / ind.signal
        self.assertNotAlmostEqual(second_value, buggy_sma, places=6)


if __name__ == "__main__":
    unittest.main()
