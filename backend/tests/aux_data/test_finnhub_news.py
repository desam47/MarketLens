"""Tests for FinnhubNewsProvider — requests.get mocked."""
import unittest
from unittest.mock import MagicMock, patch

from backend.aux_data.providers.finnhub_news import FinnhubNewsProvider


def _resp(json_body, status=200, ok=True):
    r = MagicMock()
    r.status_code = status
    r.ok = ok
    r.json.return_value = json_body
    r.text = str(json_body)
    return r


_ROWS = [
    {"datetime": 1_700_000_100, "headline": "Older", "source": "Reuters", "related": "AAPL", "url": "https://example.com/older"},
    {"datetime": 1_700_000_900, "headline": "Newer", "source": "CNBC", "related": "MSFT", "url": "https://example.com/newer"},
    {"datetime": 1_700_000_500, "headline": "", "source": "X", "related": "AAPL"},  # dropped
]


class TestFinnhubNewsProvider(unittest.TestCase):
    def setUp(self):
        self._p = patch.object(
            __import__("backend.config.settings", fromlist=["settings"]).settings.finnhub,
            "api_key", "test-key")
        self._p.start()
        self.addCleanup(self._p.stop)

    @patch("backend.aux_data.providers.finnhub_news.requests.get")
    def test_maps_sorts_and_filters(self, mock_get):
        mock_get.return_value = _resp(_ROWS)
        prov = FinnhubNewsProvider()
        out = prov.get_news("AAPL", limit=10)
        self.assertEqual(prov.name, "finnhub_news")
        self.assertEqual([i.headline for i in out.items], ["Newer", "Older"])  # newest first, blank dropped
        self.assertEqual(out.items[1].source, "Reuters")
        self.assertEqual(out.items[1].relevance, 0.75)   # AAPL in `related`
        self.assertEqual(out.items[0].relevance, 0.55)   # not related
        self.assertEqual(out.items[0].url, "https://example.com/newer")
        self.assertTrue(prov._is_healthy)

    @patch("backend.aux_data.providers.finnhub_news.requests.get")
    def test_http_error_raises_and_marks_unhealthy(self, mock_get):
        mock_get.return_value = _resp({}, status=429, ok=False)
        prov = FinnhubNewsProvider()
        with self.assertRaises(RuntimeError):
            prov.get_news("AAPL")
        self.assertFalse(prov._is_healthy)

    @patch("backend.aux_data.providers.finnhub_news.requests.get", side_effect=Exception("net"))
    def test_network_error_raises(self, _mock_get):
        with self.assertRaises(Exception):
            FinnhubNewsProvider().get_news("AAPL")

    def test_missing_key_raises(self):
        with patch.object(
            __import__("backend.config.settings", fromlist=["settings"]).settings.finnhub,
            "api_key", ""):
            with self.assertRaises(RuntimeError):
                FinnhubNewsProvider().get_news("AAPL")


if __name__ == "__main__":
    unittest.main()
