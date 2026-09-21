"""
Tests for Watchlist API endpoints
"""

import os
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from fastapi.testclient import TestClient

from backend.api.main import app


def _mock_watchlist(id=1, name="Watchlist", description="A watchlist", is_active=True):
    """Create a mock watchlist with proper attribute types for Pydantic serialization."""
    m = MagicMock()
    m.id = id
    m.name = name
    m.description = description
    m.is_active = is_active
    m.created_at = datetime(2024, 1, 1, 0, 0, 0)
    m.updated_at = datetime(2024, 1, 1, 0, 0, 0)
    return m


def _mock_symbol(id=1, watchlist_id=1, symbol="AAPL", is_enabled=True, position=0):
    """Create a mock watchlist symbol with proper attribute types."""
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


class TestWatchlistAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # Mock the database dependency (router imports get_db from
        # backend.api.dependencies, which delegates to backend.database).
        self.mock_db_patch = patch("backend.api.dependencies.get_db")
        self.mock_get_db = self.mock_db_patch.start()
        self.mock_db = MagicMock()
        self.mock_get_db.return_value = self.mock_db

        # Mock the watchlist repository
        self.mock_repo_patch = patch("backend.api.watchlist.router.WatchlistRepository")
        self.mock_repo_class = self.mock_repo_patch.start()
        self.mock_repo = MagicMock()
        self.mock_repo_class.return_value = self.mock_repo

    def tearDown(self):
        self.mock_db_patch.stop()
        self.mock_repo_patch.stop()

    def test_get_watchlists(self):
        """Test getting all watchlists"""
        # Setup mock
        self.mock_repo.get_watchlists.return_value = [
            _mock_watchlist(id=1, name="Watchlist 1", description="First watchlist"),
            _mock_watchlist(id=2, name="Watchlist 2", description="Second watchlist"),
        ]

        # Test
        response = self.client.get("/api/watchlists/")

        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["name"], "Watchlist 1")
        self.assertEqual(data[1]["name"], "Watchlist 2")

        # Verify mock was called correctly
        self.mock_repo.get_watchlists.assert_called_once_with(active_only=False)

    def test_create_watchlist(self):
        """Test creating a new watchlist"""
        # Setup mock
        self.mock_repo.create_watchlist.return_value = _mock_watchlist(
            id=1, name="New Watchlist", description="A new watchlist"
        )

        # Test
        watchlist_data = {"name": "New Watchlist", "description": "A new watchlist"}
        response = self.client.post("/api/watchlists/", json=watchlist_data)

        # Assertions
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["name"], "New Watchlist")
        self.assertEqual(data["description"], "A new watchlist")

        # Verify mock was called correctly
        self.mock_repo.create_watchlist.assert_called_once_with(
            name="New Watchlist", description="A new watchlist"
        )

    def test_get_watchlist(self):
        """Test getting a specific watchlist"""
        # Setup mock
        self.mock_repo.get_watchlist.return_value = _mock_watchlist(
            id=1, name="Test Watchlist", description="A test watchlist"
        )

        # Test
        response = self.client.get("/api/watchlists/1")

        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], 1)
        self.assertEqual(data["name"], "Test Watchlist")

        # Verify mock was called correctly
        self.mock_repo.get_watchlist.assert_called_once_with(1)

    def test_update_watchlist(self):
        """Test updating a watchlist"""
        # Setup mock
        self.mock_repo.update_watchlist.return_value = _mock_watchlist(
            id=1, name="Updated Watchlist", description="An updated watchlist"
        )

        # Test
        watchlist_data = {"name": "Updated Watchlist", "description": "An updated watchlist"}
        response = self.client.put("/api/watchlists/1", json=watchlist_data)

        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["name"], "Updated Watchlist")
        self.assertEqual(data["description"], "An updated watchlist")

        # Verify mock was called correctly
        self.mock_repo.update_watchlist.assert_called_once_with(
            watchlist_id=1,
            name="Updated Watchlist",
            description="An updated watchlist",
            is_active=None,
        )

    def test_delete_watchlist(self):
        """Test deleting a watchlist"""
        # Setup mock
        self.mock_repo.delete_watchlist.return_value = True

        # Test
        response = self.client.delete("/api/watchlists/1")

        # Assertions
        self.assertEqual(response.status_code, 204)

        # Verify mock was called correctly
        self.mock_repo.delete_watchlist.assert_called_once_with(1)

    def test_add_symbol_to_watchlist(self):
        """Test adding a symbol to a watchlist — and that the request
        returns without waiting on any provider call: register_symbol and
        enqueue_backfill (both fast, no provider I/O) are the only things
        _start_symbol_tracking_and_backfill does, and both are mocked here
        rather than left to hit the real ingestion_service singleton /
        real Redis, so this test also pins the "non-blocking" behavior the
        redesign's plan called for (previously untested — the endpoint's
        own test predates the redesign and never mocked either)."""
        mock_sym = _mock_symbol(id=1, watchlist_id=1, symbol="AAPL", is_enabled=True, position=0)
        mock_sym.entity_type = "stock"
        mock_sym.notes = None
        self.mock_repo.add_symbol_to_watchlist.return_value = (mock_sym, True, False)

        with (
            patch(
                "backend.market_data.services.ingestion_service.ingestion_service"
            ) as mock_ingestion,
            patch(
                "backend.market_data.services.backfill_queue.enqueue_backfill",
                return_value="backfill-fakejobid",
            ) as mock_enqueue,
        ):
            symbol_data = {"symbol": "AAPL", "is_enabled": True}
            response = self.client.post("/api/watchlists/1/symbols", json=symbol_data)

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["symbol"], "AAPL")
        self.assertEqual(data["watchlist_id"], 1)
        self.assertTrue(data["is_enabled"])

        self.mock_repo.add_symbol_to_watchlist.assert_called_once_with(
            watchlist_id=1,
            symbol="AAPL",
            entity_type="stock",
        )
        mock_ingestion.register_symbol.assert_called_once_with("AAPL")
        mock_enqueue.assert_called_once_with("AAPL")

    def test_add_symbol_lowercase_input_reaches_register_and_enqueue_uppercased(self):
        """A lowercase ticker in the request body must land on the
        uppercase form for every step of the chain — the stored symbol
        (via the repo call, already covered elsewhere), register_symbol,
        and enqueue_backfill — not just some of them. Previously untested;
        every existing add-path test used pre-uppercased fixtures."""
        mock_sym = _mock_symbol(id=1, watchlist_id=1, symbol="AAPL")
        self.mock_repo.add_symbol_to_watchlist.return_value = (mock_sym, True, False)

        with (
            patch(
                "backend.market_data.services.ingestion_service.ingestion_service"
            ) as mock_ingestion,
            patch(
                "backend.market_data.services.backfill_queue.enqueue_backfill",
                return_value="backfill-fakejobid",
            ) as mock_enqueue,
        ):
            response = self.client.post("/api/watchlists/1/symbols", json={"symbol": "aapl"})

        self.assertEqual(response.status_code, 201)
        mock_ingestion.register_symbol.assert_called_once_with("AAPL")
        mock_enqueue.assert_called_once_with("AAPL")

    def test_add_symbol_triggers_backfill_trigger_for_reenabled_symbol(self):
        """did_reenable=True (a previously-disabled symbol being re-enabled)
        must trigger tracking/backfill the same as a fresh add — re-enabling
        needs the same live-tracking/MQTT-subscription wake-up a new row
        gets, since disabling a symbol doesn't tear either down."""
        mock_sym = _mock_symbol(id=1, watchlist_id=1, symbol="AAPL")
        self.mock_repo.add_symbol_to_watchlist.return_value = (mock_sym, False, True)

        with (
            patch(
                "backend.market_data.services.ingestion_service.ingestion_service"
            ) as mock_ingestion,
            patch(
                "backend.market_data.services.backfill_queue.enqueue_backfill",
                return_value="backfill-fakejobid",
            ) as mock_enqueue,
        ):
            response = self.client.post("/api/watchlists/1/symbols", json={"symbol": "AAPL"})

        self.assertEqual(response.status_code, 201)
        mock_ingestion.register_symbol.assert_called_once_with("AAPL")
        mock_enqueue.assert_called_once_with("AAPL")

    def test_add_symbol_skips_backfill_trigger_for_already_enabled_symbol(self):
        """is_new_row=False AND did_reenable=False (the symbol already
        exists in this watchlist and was already enabled — a pure no-op)
        must NOT trigger tracking/backfill again."""
        mock_sym = _mock_symbol(id=1, watchlist_id=1, symbol="AAPL")
        self.mock_repo.add_symbol_to_watchlist.return_value = (mock_sym, False, False)

        with patch(
            "backend.market_data.services.ingestion_service.ingestion_service"
        ) as mock_ingestion:
            response = self.client.post("/api/watchlists/1/symbols", json={"symbol": "AAPL"})

        self.assertEqual(response.status_code, 201)
        mock_ingestion.register_symbol.assert_not_called()

    def test_remove_symbol_from_watchlist(self):
        """Test removing a symbol from a watchlist — and that it cancels
        any in-flight backfill job for that symbol (previously untested:
        the endpoint's own test never mocked or asserted on
        cancel_backfill, so a broken import path there — it's wrapped in a
        bare except-and-log-debug — would have gone unnoticed)."""
        self.mock_repo.remove_symbol_from_watchlist.return_value = True

        with patch(
            "backend.market_data.services.backfill_queue.cancel_backfill",
            return_value=True,
        ) as mock_cancel:
            response = self.client.delete("/api/watchlists/1/symbols/AAPL")

        self.assertEqual(response.status_code, 204)
        self.mock_repo.remove_symbol_from_watchlist.assert_called_once_with(
            watchlist_id=1, symbol="AAPL"
        )
        mock_cancel.assert_called_once_with("AAPL")

    def test_get_symbol_backfill_status_returns_job(self):
        """GET /symbols/{symbol}/backfill-status — 200 with the job dict
        when one exists (previously zero test coverage for this endpoint
        or its underlying get_backfill_job_status)."""
        fake_status = {
            "symbol": "AAPL",
            "job_id": "backfill-abc123",
            "status": "completed",
            "tier1_written": 10,
            "tier2_written": 5,
            "tier3_written": 2,
            "gaps_found": 0,
            "gaps_filled": 0,
            "result": None,
            "error": None,
            "created_at": "2026-09-08T00:00:00Z",
            "started_at": "2026-09-08T00:00:01Z",
            "completed_at": "2026-09-08T00:00:05Z",
        }
        with patch(
            "backend.market_data.services.backfill_queue.get_backfill_job_status",
            return_value=fake_status,
        ) as mock_status:
            response = self.client.get("/api/watchlists/symbols/AAPL/backfill-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "completed")
        mock_status.assert_called_once_with("AAPL")

    def test_get_symbol_backfill_status_404_when_no_job(self):
        """No BackfillJob for the symbol -> 404, not a 200 with nulls."""
        with patch(
            "backend.market_data.services.backfill_queue.get_backfill_job_status",
            return_value=None,
        ):
            response = self.client.get("/api/watchlists/symbols/NEVERADDED/backfill-status")

        self.assertEqual(response.status_code, 404)

    def test_trigger_watchlist_backfill(self):
        """POST /{watchlist_id}/backfill — manually (re-)triggers backfill
        for every symbol currently in the watchlist (previously zero test
        coverage for this endpoint)."""
        self.mock_repo.get_watchlist_symbols.return_value = [
            _mock_symbol(id=1, symbol="AAPL"),
            _mock_symbol(id=2, symbol="MSFT"),
        ]

        with (
            patch("backend.market_data.services.ingestion_service.ingestion_service"),
            patch(
                "backend.market_data.services.backfill_queue.enqueue_backfill",
                return_value="backfill-fakejobid",
            ) as mock_enqueue,
        ):
            response = self.client.post("/api/watchlists/1/backfill")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(sorted(data["symbols"]), ["AAPL", "MSFT"])
        self.assertEqual(data["status"], "backfill triggered")
        self.mock_repo.get_watchlist_symbols.assert_called_once_with(1, enabled_only=False)
        self.assertEqual(mock_enqueue.call_count, 2)

    def test_trigger_watchlist_backfill_404_for_missing_watchlist(self):
        self.mock_repo.get_watchlist.return_value = None
        response = self.client.post("/api/watchlists/999/backfill")
        self.assertEqual(response.status_code, 404)

    def test_enable_symbol_in_watchlist(self):
        """Test enabling a symbol in a watchlist"""
        # Setup mock
        enabled = _mock_symbol(id=1, watchlist_id=1, symbol="AAPL", is_enabled=True, position=0)
        self.mock_repo.enable_symbol_in_watchlist.return_value = True
        # Router fetches the symbol after a successful enable to return it.
        self.mock_repo.get_watchlist_symbol.return_value = enabled

        # Test
        response = self.client.put("/api/watchlists/1/symbols/AAPL/enable")

        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["symbol"], "AAPL")
        self.assertTrue(data["is_enabled"])

        # Verify mock was called correctly
        self.mock_repo.enable_symbol_in_watchlist.assert_called_once_with(
            watchlist_id=1, symbol="AAPL"
        )
        self.mock_repo.get_watchlist_symbol.assert_called_once_with(1, "AAPL")

    def test_disable_symbol_in_watchlist(self):
        """Test disabling a symbol in a watchlist"""
        # Setup mock
        disabled = _mock_symbol(id=1, watchlist_id=1, symbol="AAPL", is_enabled=False, position=0)
        self.mock_repo.disable_symbol_in_watchlist.return_value = True
        # Router fetches the symbol after a successful disable to return it.
        self.mock_repo.get_watchlist_symbol.return_value = disabled

        # Test
        response = self.client.put("/api/watchlists/1/symbols/AAPL/disable")

        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["symbol"], "AAPL")
        self.assertFalse(data["is_enabled"])

        # Verify mock was called correctly
        self.mock_repo.disable_symbol_in_watchlist.assert_called_once_with(
            watchlist_id=1, symbol="AAPL"
        )
        self.mock_repo.get_watchlist_symbol.assert_called_once_with(1, "AAPL")

    def test_reorder_watchlist_symbols(self):
        """Test reordering symbols in a watchlist"""
        # Setup mock
        self.mock_repo.reorder_watchlist_symbols.return_value = True
        self.mock_repo.get_watchlist_symbols.return_value = [
            _mock_symbol(id=1, watchlist_id=1, symbol="GOOGL", is_enabled=True, position=0),
            _mock_symbol(id=2, watchlist_id=1, symbol="AAPL", is_enabled=True, position=1),
            _mock_symbol(id=3, watchlist_id=1, symbol="MSFT", is_enabled=True, position=2),
        ]

        # Test
        symbol_order = ["GOOGL", "AAPL", "MSFT"]  # New order
        response = self.client.put("/api/watchlists/1/symbols/reorder", json=symbol_order)

        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 3)
        self.assertEqual(data[0]["symbol"], "GOOGL")
        self.assertEqual(data[1]["symbol"], "AAPL")
        self.assertEqual(data[2]["symbol"], "MSFT")

        # Verify mock was called correctly
        self.mock_repo.reorder_watchlist_symbols.assert_called_once_with(
            watchlist_id=1, symbol_order=["GOOGL", "AAPL", "MSFT"]
        )
        self.mock_repo.get_watchlist_symbols.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
