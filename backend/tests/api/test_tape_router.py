"""Tests for /api/tape/* endpoints."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.config.settings import settings


class TestTapeRouter(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_503_when_disabled(self):
        with patch.object(settings.tape, "enabled", False):
            r = self.client.get("/api/tape/AAPL")
        self.assertEqual(r.status_code, 503)

    def test_snapshot_when_enabled(self):
        fake_snap = {"symbol": "AAPL", "pressure": "heavy_buy", "signed_volume": 5000,
                     "trade_count": 40, "block_count_5m": 1, "buy_ratio": 0.78}
        with patch.object(settings.tape, "enabled", True), \
             patch("backend.api.tape.registry.get_tape_engine") as g:
            g.return_value.get_snapshot.return_value = fake_snap
            r = self.client.get("/api/tape/aapl")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["symbol"], "AAPL")
        self.assertEqual(body["snapshot"]["pressure"], "heavy_buy")
        self.assertIn("as_of", body)

    def test_bars_endpoint(self):
        with patch.object(settings.tape, "enabled", True), \
             patch("backend.repositories.tape_repository.get_tape_bars", return_value=[]):
            r = self.client.get("/api/tape/AAPL/bars?minutes=10")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"symbol": "AAPL", "bars": []})


if __name__ == "__main__":
    unittest.main()
