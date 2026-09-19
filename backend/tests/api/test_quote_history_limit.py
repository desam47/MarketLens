"""
GET /api/market-data/quote/{symbol}/history must bound ``limit``.

SQLite treats a negative LIMIT as "no limit", and the parameter used to be an
unchecked ``int``: ``?limit=-1`` (or a huge value) returned a symbol's entire
quote history (measured live: ~6k rows, 2.2 MB, up to ~0.5 s).
"""
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.api.main import app


class TestQuoteHistoryLimit(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        patcher = patch("backend.api.market_data_routes.ingestion_service")
        self.svc = patcher.start()
        self.svc.get_quote_history = MagicMock(return_value=[])
        self.addCleanup(patcher.stop)

    def _get(self, **params):
        return self.client.get("/api/market-data/quote/AAPL/history", params=params)

    def test_default_limit_is_100(self):
        self.assertEqual(self._get().status_code, 200)
        self.svc.get_quote_history.assert_called_once_with("AAPL", 100)

    def test_limit_bounds_are_inclusive(self):
        for value in (1, 1000):
            self.svc.get_quote_history.reset_mock()
            self.assertEqual(self._get(limit=value).status_code, 200, value)
            self.svc.get_quote_history.assert_called_once_with("AAPL", value)

    def test_out_of_range_limit_is_rejected_before_touching_the_db(self):
        for value in (0, -1, 1001, 100_000_000, "abc"):
            self.svc.get_quote_history.reset_mock()
            self.assertEqual(self._get(limit=value).status_code, 422, value)
            self.svc.get_quote_history.assert_not_called()


if __name__ == "__main__":
    unittest.main()
