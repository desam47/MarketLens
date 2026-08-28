"""
Tests for backend.symbols.validator.
"""
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from backend.models.market_data import DataStatus, Quote
from backend.symbols.validator import ValidationResult, validate_symbol


class TestValidateSymbol(unittest.TestCase):

    def _quote(self, symbol: str, price: float) -> Quote:
        return Quote(
            symbol=symbol,
            price=price,
            timestamp=datetime.now(),
            provider="yahoo_finance",
            data_status=DataStatus.DELAYED,
        )

    def test_validate_symbol_ok(self):
        """A symbol with a positive price is valid."""
        with patch("backend.symbols.validator.market_data_manager") as mgr:
            mgr.get_quote.return_value = self._quote("AAPL", 150.0)
            result = validate_symbol("AAPL")
        self.assertIsInstance(result, ValidationResult)
        self.assertEqual(result.symbol, "AAPL")
        self.assertTrue(result.valid)
        self.assertIsNone(result.error)
        mgr.get_quote.assert_called_once_with("AAPL")

    def test_validate_symbol_lowercase_normalized(self):
        """Lowercase input is uppercased before validation."""
        with patch("backend.symbols.validator.market_data_manager") as mgr:
            mgr.get_quote.return_value = self._quote("AAPL", 150.0)
            result = validate_symbol("aapl")
        self.assertEqual(result.symbol, "AAPL")
        self.assertTrue(result.valid)
        mgr.get_quote.assert_called_once_with("AAPL")

    def test_validate_symbol_no_price(self):
        """A symbol whose quote has price=0 is invalid with an explanatory error."""
        with patch("backend.symbols.validator.market_data_manager") as mgr:
            mgr.get_quote.return_value = self._quote("XXXX", 0.0)
            result = validate_symbol("XXXX")
        self.assertEqual(result.symbol, "XXXX")
        self.assertFalse(result.valid)
        self.assertIn("No price data", result.error or "")

    def test_validate_symbol_provider_error(self):
        """A provider exception is captured into the result; never raises."""
        with patch("backend.symbols.validator.market_data_manager") as mgr:
            mgr.get_quote.side_effect = RuntimeError("upstream timeout")
            result = validate_symbol("ZZZZ")
        self.assertEqual(result.symbol, "ZZZZ")
        self.assertFalse(result.valid)
        self.assertEqual(result.error, "upstream timeout")


if __name__ == "__main__":
    unittest.main()
