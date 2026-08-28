"""
Tests for ROC (Rate of Change) indicator
"""
import unittest

from backend.indicators.roc import ROCIndicator


class TestROCIndicator(unittest.TestCase):
    """Tests for ROCIndicator"""

    def setUp(self):
        self.indicator = ROCIndicator(period=12)

    def test_roc_calculation(self):
        """ROC = ((close - close_n_ago) / close_n_ago) * 100."""
        # 13 bars (period+1) with linearly rising prices: 100, 101, 102, ...
        # At i=12: prev=100, current=112, ROC = (112-100)/100 * 100 = 12.0
        data = [{"close": 100.0 + i} for i in range(13)]
        values = self.indicator.calculate(data)
        self.assertEqual(len(values), 1)
        self.assertAlmostEqual(values[0], 12.0, places=2)

    def test_roc_update(self):
        """update() returns latest ROC once enough bars have been seen."""
        for i in range(12):
            self.indicator.update({"close": 100.0 + i})
        # 12 updates → no ROC yet (need period+1=13)
        self.assertEqual(len(self.indicator.values), 0)
        result = self.indicator.update({"close": 112.0})
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 12.0, places=2)

    def test_roc_insufficient_data(self):
        """Fewer than period+1 bars returns empty list."""
        data = [{"close": 100.0 + i} for i in range(5)]
        values = self.indicator.calculate(data)
        self.assertEqual(values, [])


if __name__ == "__main__":
    unittest.main()
