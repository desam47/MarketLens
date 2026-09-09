"""
Tests for GET /api/system/data-quality.

Regression coverage for the 2026-09-09 duplicate-1d-bar incident: webull
stamps 1d bars at 00:00, yahoo_finance at 09:30, and those two conventions
never collided on the DB's exact-timestamp unique key — 63% of stored 1d
rows ended up duplicated before anyone noticed. This endpoint is the
ongoing watch for that class of bug recurring.
"""
import unittest


class TestDataQualityEndpoint(unittest.TestCase):
    def setUp(self):
        # Importing app lazily so conftest.py's autouse fixtures have
        # a chance to run first.
        from fastapi.testclient import TestClient
        from backend.api.main import app
        self.client = TestClient(app)

    def test_endpoint_returns_200(self):
        r = self.client.get("/api/system/data-quality")
        self.assertEqual(r.status_code, 200)

    def test_response_shape(self):
        r = self.client.get("/api/system/data-quality")
        data = r.json()
        self.assertIn("healthy", data)
        self.assertIn("duplicate_count", data)
        self.assertIn("duplicates", data)
        self.assertIsInstance(data["healthy"], bool)
        self.assertIsInstance(data["duplicate_count"], int)
        self.assertIsInstance(data["duplicates"], list)

    def test_healthy_flag_matches_duplicate_count(self):
        r = self.client.get("/api/system/data-quality")
        data = r.json()
        self.assertEqual(data["healthy"], data["duplicate_count"] == 0)
        self.assertEqual(data["duplicate_count"], len(data["duplicates"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
