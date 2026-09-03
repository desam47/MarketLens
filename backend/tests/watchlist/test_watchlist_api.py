"""
Tests for Watchlist API endpoints
"""
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from fastapi.testclient import TestClient

from backend.api.main import app


def _mock_watchlist(id=1, name="Watchlist", description="A watchlist",
                    is_active=True):
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
        self.mock_db_patch = patch('backend.api.dependencies.get_db')
        self.mock_get_db = self.mock_db_patch.start()
        self.mock_db = MagicMock()
        self.mock_get_db.return_value = self.mock_db

        # Mock the watchlist repository
        self.mock_repo_patch = patch('backend.api.watchlist.router.WatchlistRepository')
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
        watchlist_data = {
            "name": "New Watchlist",
            "description": "A new watchlist"
        }
        response = self.client.post("/api/watchlists/", json=watchlist_data)

        # Assertions
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["name"], "New Watchlist")
        self.assertEqual(data["description"], "A new watchlist")

        # Verify mock was called correctly
        self.mock_repo.create_watchlist.assert_called_once_with(
            name="New Watchlist",
            description="A new watchlist"
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
        watchlist_data = {
            "name": "Updated Watchlist",
            "description": "An updated watchlist"
        }
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
        """Test adding a symbol to a watchlist"""
        # Setup mock — Phase 3.3.14 returns (symbol, is_new_row)
        mock_sym = _mock_symbol(
            id=1, watchlist_id=1, symbol="AAPL", is_enabled=True, position=0
        )
        mock_sym.entity_type = "stock"
        mock_sym.notes = None
        self.mock_repo.add_symbol_to_watchlist.return_value = (mock_sym, True)

        # Test
        symbol_data = {
            "symbol": "AAPL",
            "is_enabled": True
        }
        response = self.client.post("/api/watchlists/1/symbols", json=symbol_data)

        # Assertions
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["symbol"], "AAPL")
        self.assertEqual(data["watchlist_id"], 1)
        self.assertTrue(data["is_enabled"])

        # Verify mock was called correctly (router passes symbol and entity_type)
        self.mock_repo.add_symbol_to_watchlist.assert_called_once_with(
            watchlist_id=1,
            symbol="AAPL",
            entity_type="stock",
        )

    def test_remove_symbol_from_watchlist(self):
        """Test removing a symbol from a watchlist"""
        # Setup mock
        self.mock_repo.remove_symbol_from_watchlist.return_value = True

        # Test
        response = self.client.delete("/api/watchlists/1/symbols/AAPL")

        # Assertions
        self.assertEqual(response.status_code, 204)

        # Verify mock was called correctly
        self.mock_repo.remove_symbol_from_watchlist.assert_called_once_with(
            watchlist_id=1,
            symbol="AAPL"
        )

    def test_enable_symbol_in_watchlist(self):
        """Test enabling a symbol in a watchlist"""
        # Setup mock
        enabled = _mock_symbol(
            id=1, watchlist_id=1, symbol="AAPL", is_enabled=True, position=0
        )
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
            watchlist_id=1,
            symbol="AAPL"
        )
        self.mock_repo.get_watchlist_symbol.assert_called_once_with(1, "AAPL")

    def test_disable_symbol_in_watchlist(self):
        """Test disabling a symbol in a watchlist"""
        # Setup mock
        disabled = _mock_symbol(
            id=1, watchlist_id=1, symbol="AAPL", is_enabled=False, position=0
        )
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
            watchlist_id=1,
            symbol="AAPL"
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
        response = self.client.put("/api/watchlists/1/symbols/reorder",
                                  json=symbol_order)

        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 3)
        self.assertEqual(data[0]["symbol"], "GOOGL")
        self.assertEqual(data[1]["symbol"], "AAPL")
        self.assertEqual(data[2]["symbol"], "MSFT")

        # Verify mock was called correctly
        self.mock_repo.reorder_watchlist_symbols.assert_called_once_with(
            watchlist_id=1,
            symbol_order=["GOOGL", "AAPL", "MSFT"]
        )
        self.mock_repo.get_watchlist_symbols.assert_called_once_with(1)

if __name__ == '__main__':
    unittest.main()

