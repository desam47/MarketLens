"""
Tests for /api/ai/digest/* — the daily/session AI digest read + manual
-trigger endpoints.
"""
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.api.main import app


class TestGetLatestDigest(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    @patch("backend.api.ai.digest_router.AIDigestRepository")
    def test_returns_404_when_no_digest_exists(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_latest.return_value = None
        mock_repo_cls.return_value = mock_repo

        resp = self.client.get("/api/ai/digest/latest?session=close")
        self.assertEqual(resp.status_code, 404)

    @patch("backend.api.ai.digest_router.AIDigestRepository")
    def test_returns_latest_digest(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_row = MagicMock(
            id=1, session="close", market_regime="RISK_ON",
            narrative="Markets are calm.",
            payload='{"movers": {"top_bullish": []}}',
            generated_at=datetime(2026, 9, 9, 16, 15, tzinfo=UTC),
        )
        mock_repo.get_latest.return_value = mock_row
        mock_repo_cls.return_value = mock_repo

        resp = self.client.get("/api/ai/digest/latest?session=close")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["session"], "close")
        self.assertEqual(data["market_regime"], "RISK_ON")
        self.assertEqual(data["narrative"], "Markets are calm.")
        self.assertIn("movers", data["payload"])

    def test_invalid_session_rejected(self):
        resp = self.client.get("/api/ai/digest/latest?session=lunchtime")
        self.assertEqual(resp.status_code, 422)

    @patch("backend.api.ai.digest_router.AIDigestRepository")
    def test_defaults_to_close_session(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_latest.return_value = None
        mock_repo_cls.return_value = mock_repo

        self.client.get("/api/ai/digest/latest")
        mock_repo.get_latest.assert_called_once_with("close")


class TestGetDigestHistory(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    @patch("backend.api.ai.digest_router.AIDigestRepository")
    def test_returns_history_list(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_history.return_value = [
            MagicMock(
                id=2, session="close", market_regime="NEUTRAL", narrative="n2",
                payload=None, generated_at=datetime(2026, 9, 9, tzinfo=UTC),
            ),
            MagicMock(
                id=1, session="premarket", market_regime="RISK_ON", narrative="n1",
                payload=None, generated_at=datetime(2026, 9, 8, tzinfo=UTC),
            ),
        ]
        mock_repo_cls.return_value = mock_repo

        resp = self.client.get("/api/ai/digest/history?limit=5")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["id"], 2)

    @patch("backend.api.ai.digest_router.AIDigestRepository")
    def test_null_payload_handled(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_history.return_value = [
            MagicMock(
                id=1, session="close", market_regime=None, narrative=None,
                payload=None, generated_at=datetime(2026, 9, 9, tzinfo=UTC),
            ),
        ]
        mock_repo_cls.return_value = mock_repo

        resp = self.client.get("/api/ai/digest/history")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()[0]["payload"])


class TestGenerateDigestNow(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    @patch("backend.ai.digest.generate_and_store_digest")
    def test_generates_and_returns_digest(self, mock_generate):
        mock_generate.return_value = {
            "id": 1,
            "session": "close",
            "generated_at": datetime(2026, 9, 9, 16, 15, tzinfo=UTC),
            "market_regime": "RISK_ON",
            "narrative": "All clear.",
            "headline_movers": ["AAPL"],
            "payload": {"movers": {}},
        }

        resp = self.client.post("/api/ai/digest/generate?session=close")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["narrative"], "All clear.")
        mock_generate.assert_called_once_with("close")


if __name__ == "__main__":
    unittest.main()
