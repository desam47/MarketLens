"""
Tests for backend/market_data/providers/finnhub_provider.py — FinnhubProvider

All HTTP calls are mocked. We exercise the public API surface
(``get_quote``, ``get_historical_bars``, ``get_batch_quotes``,
``get_market_status``, ``is_available``) plus error-handling
behaviour (HTTP 429, 4xx, 5xx, empty responses).
"""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from backend.market_data.providers.finnhub_provider import FinnhubProvider


def _mock_response(status_code=200, json_data=None, text="") -> MagicMock:
    """Build a requests.Response-like mock with .ok, .status_code, .json()."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 300
    resp.text = text or ""
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


class TestFinnhubProviderQuote(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.providers.finnhub_provider._settings") as mock_settings:
            mock_settings.finnhub.api_key = ""
            mock_settings.finnhub.request_timeout = 10.0
            self.provider = FinnhubProvider()

    @patch("backend.market_data.providers.finnhub_provider.requests.get")
    def test_get_quote_returns_quote_with_price(self, mock_get):
        mock_get.return_value = _mock_response(200, {
            "c": 150.50, "d": 1.5, "dp": 1.0, "h": 151.0,
            "l": 149.0, "o": 149.5, "pc": 149.0, "t": 1700000000
        })
        quote = self.provider.get_quote("AAPL")
        self.assertEqual(quote.symbol, "AAPL")
        self.assertEqual(quote.price, 150.50)
        self.assertEqual(quote.provider, "finnhub")
        self.assertEqual(quote.data_status.value, "DELAYED")

    @patch("backend.market_data.providers.finnhub_provider.requests.get")
    def test_get_quote_raises_on_429(self, mock_get):
        mock_get.return_value = _mock_response(429, {}, "rate limit")
        with self.assertRaises(RuntimeError) as ctx:
            self.provider.get_quote("AAPL")
        self.assertIn("rate limited", str(ctx.exception).lower())

    @patch("backend.market_data.providers.finnhub_provider.requests.get")
    def test_get_quote_raises_on_5xx(self, mock_get):
        mock_get.return_value = _mock_response(500, {}, "internal error")
        with self.assertRaises(RuntimeError):
            self.provider.get_quote("AAPL")

    @patch("backend.market_data.providers.finnhub_provider.requests.get")
    def test_get_quote_raises_on_empty_response(self, mock_get):
        mock_get.return_value = _mock_response(200, {})
        with self.assertRaises(ValueError):
            self.provider.get_quote("INVALID")


class TestFinnhubProviderBars(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.providers.finnhub_provider._settings") as mock_settings:
            mock_settings.finnhub.api_key = ""
            mock_settings.finnhub.request_timeout = 10.0
            self.provider = FinnhubProvider()

    @patch("backend.market_data.providers.finnhub_provider.requests.get")
    def test_get_historical_bars_returns_bar_list(self, mock_get):
        mock_get.return_value = _mock_response(200, {
            "s": "ok",
            "t": [1700000000, 1700086400],
            "o": [100.0, 101.0],
            "h": [101.5, 102.0],
            "l": [99.5, 100.5],
            "c": [101.0, 101.5],
            "v": [1000000, 1500000],
        })
        bars = self.provider.get_historical_bars("AAPL", timeframe="1d", range_="3mo")
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].open, 100.0)
        self.assertEqual(bars[1].close, 101.5)
        self.assertEqual(bars[0].provider, "finnhub")

    @patch("backend.market_data.providers.finnhub_provider.requests.get")
    def test_get_historical_bars_returns_empty_on_no_data(self, mock_get):
        mock_get.return_value = _mock_response(200, {"s": "no_data"})
        bars = self.provider.get_historical_bars("INVALID", timeframe="1d")
        self.assertEqual(bars, [])

    @patch("backend.market_data.providers.finnhub_provider.requests.get")
    def test_get_latest_bar_returns_one_bar(self, mock_get):
        mock_get.return_value = _mock_response(200, {
            "s": "ok",
            "t": [1700000000],
            "o": [100.0], "h": [101.0], "l": [99.0], "c": [100.5], "v": [1000000]
        })
        bar = self.provider.get_latest_bar("AAPL", "1d")
        self.assertEqual(bar.close, 100.5)
        self.assertEqual(bar.timeframe, "1d")

    def test_unsupported_timeframe_raises(self):
        with self.assertRaises(ValueError):
            # Force resolution lookup to fail by patching a private helper
            with patch("backend.market_data.providers.finnhub_provider._resolve_resolution",
                       side_effect=ValueError("Unsupported timeframe '7y'")):
                self.provider.get_historical_bars("AAPL", timeframe="7y")


class TestFinnhubProviderBatchQuotes(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.providers.finnhub_provider._settings") as mock_settings:
            mock_settings.finnhub.api_key = ""
            mock_settings.finnhub.request_timeout = 10.0
            self.provider = FinnhubProvider()

    @patch("backend.market_data.providers.finnhub_provider.requests.get")
    def test_batch_quotes_returns_dict(self, mock_get):
        # First call returns valid, second returns empty (no_data for invalid symbol)
        mock_get.side_effect = [
            _mock_response(200, {"c": 150.0, "t": 1700000000, "pc": 149.0}),
            _mock_response(200, {}),  # empty for INVALID
        ]
        result = self.provider.get_batch_quotes(["AAPL", "INVALID"])
        self.assertEqual(len(result), 2)
        self.assertEqual(result["AAPL"].price, 150.0)
        self.assertEqual(result["INVALID"].data_status.value, "ERROR")

    @patch("backend.market_data.providers.finnhub_provider.requests.get")
    def test_batch_quotes_empty_list(self, mock_get):
        result = self.provider.get_batch_quotes([])
        self.assertEqual(result, {})
        mock_get.assert_not_called()


class TestFinnhubProviderMarketStatus(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.providers.finnhub_provider._settings") as mock_settings:
            mock_settings.finnhub.api_key = ""
            mock_settings.finnhub.request_timeout = 10.0
            self.provider = FinnhubProvider()

    @patch("backend.market_data.providers.finnhub_provider.requests.get")
    def test_get_market_status_open(self, mock_get):
        mock_get.return_value = _mock_response(200, {"session": "regular"})
        status = self.provider.get_market_status("AAPL")
        self.assertTrue(status.is_open)
        self.assertEqual(status.provider, "finnhub")

    @patch("backend.market_data.providers.finnhub_provider.requests.get")
    def test_get_market_status_closed(self, mock_get):
        mock_get.return_value = _mock_response(200, {"session": "closed"})
        status = self.provider.get_market_status("AAPL")
        self.assertFalse(status.is_open)


class TestFinnhubProviderAvailability(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.providers.finnhub_provider._settings") as mock_settings:
            mock_settings.finnhub.api_key = ""
            mock_settings.finnhub.request_timeout = 10.0
            self.provider = FinnhubProvider()

    def test_is_available_true_on_valid_quote(self):
        with patch("backend.market_data.providers.finnhub_provider.requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, {"c": 150.0, "t": 1700000000, "pc": 149.0})
            self.assertTrue(self.provider.is_available())

    def test_is_available_false_on_error(self):
        with patch("backend.market_data.providers.finnhub_provider.requests.get") as mock_get:
            mock_get.return_value = _mock_response(429, {}, "rate limit")
            self.assertFalse(self.provider.is_available())


class TestFinnhubProviderCapabilities(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.providers.finnhub_provider._settings") as mock_settings:
            mock_settings.finnhub.api_key = ""
            mock_settings.finnhub.request_timeout = 10.0
            self.provider = FinnhubProvider()

    def test_get_capabilities(self):
        caps = self.provider.get_capabilities()
        self.assertEqual(caps.provider_name, "finnhub")
        self.assertTrue(caps.supports_historical_bars)
        self.assertTrue(caps.supports_batch_quotes)


if __name__ == "__main__":
    unittest.main()
