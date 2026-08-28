"""
Tests for WatchlistRepository
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from backend.models.watchlist import Watchlist, WatchlistSymbol
from backend.repositories.watchlist_repository import WatchlistRepository


class TestWatchlistRepository(unittest.TestCase):

    def setUp(self):
        # Create a mock database session
        self.mock_db = MagicMock()
        self.repo = WatchlistRepository(self.mock_db)

    def test_get_watchlists(self):
        """Test getting all watchlists"""
        # Setup mock
        mock_watchlists = [MagicMock(spec=Watchlist), MagicMock(spec=Watchlist)]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value.all.return_value = mock_watchlists
        self.mock_db.query.return_value = mock_query

        # Test
        result = self.repo.get_watchlists()

        # Assertions
        self.assertEqual(len(result), 2)
        self.mock_db.query.assert_called_once_with(Watchlist)
        mock_query.filter.assert_called_once()
        mock_query.order_by.assert_called_once()
        mock_query.order_by.return_value.all.assert_called_once()

    def test_get_watchlist(self):
        """Test getting a specific watchlist"""
        # Setup mock
        mock_watchlist = MagicMock(spec=Watchlist)
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_watchlist
        self.mock_db.query.return_value = mock_query

        # Test
        result = self.repo.get_watchlist(1)

        # Assertions
        self.assertEqual(result, mock_watchlist)
        self.mock_db.query.assert_called_once_with(Watchlist)
        mock_query.filter.assert_called_once()
        mock_query.filter.return_value.first.assert_called_once()

    def test_create_watchlist(self):
        """Test creating a new watchlist"""
        # Setup mock
        mock_watchlist = MagicMock(spec=Watchlist)
        mock_watchlist.id = 1
        mock_watchlist.name = "Test Watchlist"
        mock_watchlist.description = "A test watchlist"
        self.mock_db.add = MagicMock()
        self.mock_db.commit = MagicMock()
        self.mock_db.refresh = MagicMock()

        # Patch Watchlist constructor
        with patch('backend.repositories.watchlist_repository.Watchlist', return_value=mock_watchlist):
            # Test
            result = self.repo.create_watchlist("Test Watchlist", "A test watchlist")

            # Assertions
            self.assertEqual(result, mock_watchlist)
            self.mock_db.add.assert_called_once_with(mock_watchlist)
            self.mock_db.commit.assert_called_once()
            self.mock_db.refresh.assert_called_once_with(mock_watchlist)

    def test_add_symbol_to_watchlist_new(self):
        """Test adding a new symbol to a watchlist"""
        # Setup mocks
        mock_watchlist_symbol = MagicMock(spec=WatchlistSymbol)
        mock_watchlist_symbol.id = 1
        mock_watchlist_symbol.watchlist_id = 1
        mock_watchlist_symbol.symbol = "AAPL"
        self.repo.get_watchlist_symbol = MagicMock(return_value=None)  # Symbol doesn't exist
        self.mock_db.add = MagicMock()
        self.mock_db.commit = MagicMock()
        self.mock_db.refresh = MagicMock()

        # Patch WatchlistSymbol constructor
        with patch('backend.repositories.watchlist_repository.WatchlistSymbol', return_value=mock_watchlist_symbol):
            # Test
            result = self.repo.add_symbol_to_watchlist(1, "AAPL")

            # Assertions
            self.assertEqual(result, mock_watchlist_symbol)
            self.repo.get_watchlist_symbol.assert_called_once_with(1, "AAPL")
            self.mock_db.add.assert_called_once_with(mock_watchlist_symbol)
            self.mock_db.commit.assert_called_once()
            self.mock_db.refresh.assert_called_once_with(mock_watchlist_symbol)

    def test_add_symbol_to_watchlist_existing_disabled(self):
        """Test re-enabling an existing disabled symbol in a watchlist"""
        # Setup mocks
        mock_watchlist_symbol = MagicMock(spec=WatchlistSymbol)
        mock_watchlist_symbol.id = 1
        mock_watchlist_symbol.watchlist_id = 1
        mock_watchlist_symbol.symbol = "AAPL"
        mock_watchlist_symbol.is_enabled = False  # Currently disabled
        self.repo.get_watchlist_symbol = MagicMock(return_value=mock_watchlist_symbol)
        self.mock_db.commit = MagicMock()
        self.mock_db.refresh = MagicMock()

        # Test
        result = self.repo.add_symbol_to_watchlist(1, "AAPL")

        # Assertions
        self.assertEqual(result, mock_watchlist_symbol)
        self.repo.get_watchlist_symbol.assert_called_once_with(1, "AAPL")
        self.assertTrue(mock_watchlist_symbol.is_enabled)  # Should be re-enabled
        self.mock_db.commit.assert_called_once()
        self.mock_db.refresh.assert_called_once_with(mock_watchlist_symbol)

    def test_remove_symbol_from_watchlist(self):
        """Test removing a symbol from a watchlist"""
        # Setup mocks
        mock_watchlist_symbol = MagicMock(spec=WatchlistSymbol)
        mock_watchlist_symbol.id = 1
        mock_watchlist_symbol.watchlist_id = 1
        mock_watchlist_symbol.symbol = "AAPL"
        mock_watchlist_symbol.is_enabled = True
        self.repo.get_watchlist_symbol = MagicMock(return_value=mock_watchlist_symbol)
        self.mock_db.commit = MagicMock()

        # Test
        result = self.repo.remove_symbol_from_watchlist(1, "AAPL")

        # Assertions
        self.assertTrue(result)
        self.repo.get_watchlist_symbol.assert_called_once_with(1, "AAPL")
        self.assertFalse(mock_watchlist_symbol.is_enabled)  # Should be disabled
        self.mock_db.commit.assert_called_once()

    def test_reorder_watchlist_symbols(self):
        """Test reordering symbols in a watchlist"""
        # Setup mocks — mock symbols
        mock_symbol_aapl = MagicMock(spec=WatchlistSymbol)
        mock_symbol_aapl.id = 1
        mock_symbol_aapl.watchlist_id = 1
        mock_symbol_aapl.symbol = "AAPL"

        mock_symbol_googl = MagicMock(spec=WatchlistSymbol)
        mock_symbol_googl.id = 2
        mock_symbol_googl.watchlist_id = 1
        mock_symbol_googl.symbol = "GOOGL"

        self.repo.get_watchlist_symbol = MagicMock(side_effect=[mock_symbol_googl, mock_symbol_aapl])
        self.mock_db.commit = MagicMock()

        # Test
        result = self.repo.reorder_watchlist_symbols(1, ["GOOGL", "AAPL"])  # GOOGL first, then AAPL

        # Assertions
        self.assertTrue(result)
        # Should be called for each symbol in the order
        self.assertEqual(self.repo.get_watchlist_symbol.call_count, 2)
        self.repo.get_watchlist_symbol.assert_any_call(1, "GOOGL")
        self.repo.get_watchlist_symbol.assert_any_call(1, "AAPL")
        # Check positions were set correctly
        self.assertEqual(mock_symbol_googl.position, 0)  # First in list
        self.assertEqual(mock_symbol_aapl.position, 1)   # Second in list
        self.mock_db.commit.assert_called_once()

if __name__ == '__main__':
    unittest.main()
