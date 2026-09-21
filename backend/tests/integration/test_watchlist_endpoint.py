"""
End-to-end watchlist API integration tests.

Tests the full CRUD lifecycle via the FastAPI TestClient: create a
watchlist, add symbols, rename it, delete it.
"""

from __future__ import annotations

import os
import sys
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("OBSERVABILITY_TRACING_ENABLED", "false")


class TestWatchlistCRUD(unittest.TestCase):
    """Full create → read → update → delete lifecycle for watchlists."""

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from backend.api.main import app

        self.client = TestClient(app)

    def test_create_and_list_watchlist(self) -> None:
        # Create a named watchlist.
        resp = self.client.post(
            "/api/watchlists/",
            json={"name": "Integration Test WL"},
        )
        self.assertEqual(resp.status_code, 201, msg=resp.text)
        body = resp.json()
        self.assertEqual(body["name"], "Integration Test WL")
        wl_id = body["id"]

        # List all watchlists and find the one we just created.
        resp = self.client.get("/api/watchlists/")
        self.assertEqual(resp.status_code, 200, msg=resp.text)
        names = [wl["name"] for wl in resp.json()]
        self.assertIn("Integration Test WL", names)

        # Cleanup.
        self.client.delete(f"/api/watchlists/{wl_id}")

    def test_get_watchlist_by_id(self) -> None:
        # Create a watchlist.
        resp = self.client.post("/api/watchlists/", json={"name": "By ID Test"})
        self.assertEqual(resp.status_code, 201)
        wl_id = resp.json()["id"]

        # Fetch it by ID.
        resp = self.client.get(f"/api/watchlists/{wl_id}")
        self.assertEqual(resp.status_code, 200, msg=resp.text)
        self.assertEqual(resp.json()["name"], "By ID Test")

        # Cleanup.
        self.client.delete(f"/api/watchlists/{wl_id}")

    def test_rename_watchlist(self) -> None:
        # Create a watchlist.
        resp = self.client.post("/api/watchlists/", json={"name": "Old Name"})
        self.assertEqual(resp.status_code, 201)
        wl_id = resp.json()["id"]

        # Rename it.
        resp = self.client.put(
            f"/api/watchlists/{wl_id}",
            json={"name": "New Name"},
        )
        self.assertEqual(resp.status_code, 200, msg=resp.text)
        self.assertEqual(resp.json()["name"], "New Name")

        # Cleanup.
        self.client.delete(f"/api/watchlists/{wl_id}")

    def test_delete_watchlist(self) -> None:
        # Create a watchlist.
        resp = self.client.post("/api/watchlists/", json={"name": "Delete Me"})
        self.assertEqual(resp.status_code, 201)
        wl_id = resp.json()["id"]

        # Delete it.
        resp = self.client.delete(f"/api/watchlists/{wl_id}")
        self.assertEqual(resp.status_code, 204, msg=resp.text)

        # Phase 3.3.15: hard delete removes the row, so a second delete now
        # returns 404 (the row is genuinely gone, not just soft-disabled).
        resp = self.client.delete(f"/api/watchlists/{wl_id}")
        self.assertEqual(resp.status_code, 404, msg=resp.text)

    def test_add_symbol_to_watchlist(self) -> None:
        # Create a watchlist.
        resp = self.client.post("/api/watchlists/", json={"name": "Symbols Test"})
        self.assertEqual(resp.status_code, 201)
        wl_id = resp.json()["id"]

        # Add a symbol.
        resp = self.client.post(
            f"/api/watchlists/{wl_id}/symbols",
            json={"symbol": "AAPL", "notes": ""},
        )
        self.assertIn(resp.status_code, (200, 201), msg=resp.text)
        body = resp.json()
        self.assertEqual(body["symbol"], "AAPL")

        # Cleanup.
        self.client.delete(f"/api/watchlists/{wl_id}")

    def test_get_symbols_for_watchlist(self) -> None:
        # Create a watchlist.
        resp = self.client.post("/api/watchlists/", json={"name": "List Symbols"})
        self.assertEqual(resp.status_code, 201)
        wl_id = resp.json()["id"]

        # Add two symbols.
        self.client.post(f"/api/watchlists/{wl_id}/symbols", json={"symbol": "TSLA", "notes": ""})
        self.client.post(f"/api/watchlists/{wl_id}/symbols", json={"symbol": "NVDA", "notes": ""})

        # List symbols.
        resp = self.client.get(f"/api/watchlists/{wl_id}/symbols")
        self.assertEqual(resp.status_code, 200, msg=resp.text)
        symbols = [s["symbol"] for s in resp.json()]
        self.assertIn("TSLA", symbols)
        self.assertIn("NVDA", symbols)

        # Cleanup.
        self.client.delete(f"/api/watchlists/{wl_id}")

    def test_delete_nonexistent_watchlist_returns_404(self) -> None:
        resp = self.client.delete("/api/watchlists/999999")
        self.assertEqual(resp.status_code, 404, msg=resp.text)


if __name__ == "__main__":
    unittest.main()
