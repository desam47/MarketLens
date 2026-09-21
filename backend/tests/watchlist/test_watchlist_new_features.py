"""
Tests for Phase 3 closure endpoints: search, import, export.

Follows the same mock-everything pattern as test_watchlist_api.py.
"""

import os
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from fastapi.testclient import TestClient

from backend.api.main import app


def _mock_symbol(id=1, watchlist_id=1, symbol="AAPL", is_enabled=True, position=0):
    m = MagicMock()
    m.id = id
    m.watchlist_id = watchlist_id
    m.symbol = symbol
    m.is_enabled = is_enabled
    m.position = position
    m.added_at = datetime(2024, 1, 1, 0, 0, 0)
    m.entity_type = "stock"
    m.notes = None
    return m


class TestWatchlistNewFeatures(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.db_patch = patch("backend.api.dependencies.get_db")
        self.mock_get_db = self.db_patch.start()
        self.mock_db = MagicMock()
        self.mock_get_db.return_value = self.mock_db

        self.repo_patch = patch("backend.api.watchlist.router.WatchlistRepository")
        self.mock_repo_class = self.repo_patch.start()
        self.mock_repo = MagicMock()
        self.mock_repo_class.return_value = self.mock_repo

    def tearDown(self):
        self.db_patch.stop()
        self.repo_patch.stop()

    # ------------------------------------------------------------------
    # GET /api/watchlists/{id}/symbols/search
    # ------------------------------------------------------------------

    def test_search_returns_matching_symbols(self):
        aapl = _mock_symbol(id=1, symbol="AAPL")
        msft = _mock_symbol(id=2, symbol="MSFT")
        spy = _mock_symbol(id=3, symbol="SPY")
        self.mock_repo.get_watchlist.return_value = MagicMock()
        self.mock_repo.get_all_watchlist_symbols.return_value = [aapl, msft, spy]

        response = self.client.get("/api/watchlists/1/symbols/search?q=AA")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([s["symbol"] for s in data], ["AAPL"])

    def test_search_is_case_insensitive(self):
        aapl = _mock_symbol(id=1, symbol="AAPL")
        self.mock_repo.get_watchlist.return_value = MagicMock()
        self.mock_repo.get_all_watchlist_symbols.return_value = [aapl]

        response = self.client.get("/api/watchlists/1/symbols/search?q=aa")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)

    def test_search_includes_disabled_symbols(self):
        aapl_enabled = _mock_symbol(id=1, symbol="AAPL", is_enabled=True)
        appl_disabled = _mock_symbol(id=2, symbol="APPL", is_enabled=False)
        self.mock_repo.get_watchlist.return_value = MagicMock()
        self.mock_repo.get_all_watchlist_symbols.return_value = [aapl_enabled, appl_disabled]

        response = self.client.get("/api/watchlists/1/symbols/search?q=AP")

        self.assertEqual(response.status_code, 200)
        symbols = {s["symbol"] for s in response.json()}
        self.assertEqual(symbols, {"AAPL", "APPL"})

    def test_search_404_for_missing_watchlist(self):
        self.mock_repo.get_watchlist.return_value = None
        response = self.client.get("/api/watchlists/999/symbols/search?q=AA")
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # POST /api/watchlists/{id}/import
    # ------------------------------------------------------------------

    def _setup_import(self, existing=None, enabled_count=0, max_symbols=50):
        self.mock_repo.get_watchlist.return_value = MagicMock()
        # Treat any returned symbol as "already present" (which the test
        # then asserts is correctly classified as skipped).
        self.mock_repo.get_watchlist_symbol.side_effect = lambda wl_id, sym: (
            existing.get(sym) if existing else None
        )
        self.mock_repo.get_watchlist_symbol_count.return_value = enabled_count
        # The router unpacks this as
        # `_, is_new_row, did_reenable = repo.add_symbol_to_watchlist(...)`
        # — an unconfigured MagicMock's default __iter__ yields 0 items, which
        # makes that unpack raise "not enough values to unpack (expected 3, got 0)".
        self.mock_repo.add_symbol_to_watchlist.side_effect = lambda wl_id, sym, **kw: (
            _mock_symbol(symbol=sym),
            True,
            False,
        )

    def test_import_imports_valid_skips_duplicates(self):
        # NVDA is already present and enabled -> skipped.
        # AAPL is new -> imported.
        # FAKEX provider check is mocked in the per-test patch.
        existing = {"NVDA": _mock_symbol(id=1, symbol="NVDA", is_enabled=True)}
        self._setup_import(existing=existing)

        with patch("backend.api.watchlist.router.validate_symbol") as mock_validate:
            from backend.symbols.validator import ValidationResult

            mock_validate.side_effect = lambda s: ValidationResult(
                symbol=s, valid=s != "FAKEX", error=("bad" if s == "FAKEX" else None)
            )

            response = self.client.post(
                "/api/watchlists/1/import",
                json={"symbols": ["AAPL", "NVDA", "FAKEX"]},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["imported"], ["AAPL"])
        self.assertEqual(data["skipped"], ["NVDA"])
        self.assertEqual(len(data["errors"]), 1)
        self.assertIn("FAKEX", data["errors"][0])

    def test_import_respects_max_symbols(self):
        # Watchlist is "full" (0 slots left) -> every valid symbol errors.
        self._setup_import(enabled_count=50, max_symbols=50)

        with patch("backend.api.watchlist.router.validate_symbol") as mock_validate:
            from backend.symbols.validator import ValidationResult

            mock_validate.return_value = ValidationResult(symbol="AAPL", valid=True)

            response = self.client.post(
                "/api/watchlists/1/import",
                json={"symbols": ["AAPL", "NVDA"]},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["imported"], [])
        self.assertEqual(data["skipped"], [])
        self.assertEqual(len(data["errors"]), 2)
        self.assertTrue(all("watchlist full" in e for e in data["errors"]))

    def test_import_404_for_missing_watchlist(self):
        self.mock_repo.get_watchlist.return_value = None
        response = self.client.post("/api/watchlists/999/import", json={"symbols": ["AAPL"]})
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # GET /api/watchlists/{id}/export
    # ------------------------------------------------------------------

    def test_export_json_returns_symbol_list(self):
        aapl = _mock_symbol(id=1, symbol="AAPL")
        msft = _mock_symbol(id=2, symbol="MSFT")
        self.mock_repo.get_watchlist.return_value = MagicMock()
        self.mock_repo.get_all_watchlist_symbols.return_value = [aapl, msft]

        response = self.client.get("/api/watchlists/1/export?format=json")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([s["symbol"] for s in data], ["AAPL", "MSFT"])

    def test_export_csv_returns_csv_body(self):
        aapl = _mock_symbol(id=1, symbol="AAPL", is_enabled=True, position=0)
        msft = _mock_symbol(id=2, symbol="MSFT", is_enabled=False, position=1)
        self.mock_repo.get_watchlist.return_value = MagicMock()
        self.mock_repo.get_all_watchlist_symbols.return_value = [aapl, msft]

        response = self.client.get("/api/watchlists/1/export?format=csv")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/csv"))
        self.assertIn("attachment", response.headers["content-disposition"])
        body = response.text
        self.assertIn("symbol,is_enabled,position", body)
        self.assertIn("AAPL,1,0", body)
        self.assertIn("MSFT,0,1", body)

    def test_export_404_for_missing_watchlist(self):
        self.mock_repo.get_watchlist.return_value = None
        response = self.client.get("/api/watchlists/999/export?format=json")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
