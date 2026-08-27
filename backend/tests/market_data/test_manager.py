"""
Tests for MarketDataManager
"""
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../'))

from backend.market_data.services.manager import MarketDataManager
from backend.models.market_data import (
    DataStatus,
    ProviderStatus,
    Quote,
)


class TestMarketDataManager(unittest.TestCase):
    
    def setUp(self):
        self.manager = MarketDataManager()
    
    def test_initialization(self):
        """Test that manager initializes with Yahoo Finance provider"""
        self.assertIn("yahoo_finance", self.manager.providers)
        self.assertEqual(len(self.manager.provider_priority), 1)
        self.assertEqual(self.manager.provider_priority[0], "yahoo_finance")
    
    def test_add_provider(self):
        """Test adding a new provider"""
        # Create a mock provider
        mock_provider = MagicMock()
        mock_provider.name = "mock_provider"
        mock_provider.is_available.return_value = True
        
        initial_count = len(self.manager.providers)
        self.manager.add_provider(mock_provider, priority=0)
        
        self.assertEqual(len(self.manager.providers), initial_count + 1)
        self.assertIn("mock_provider", self.manager.providers)
        self.assertIn("mock_provider", self.manager.provider_priority)
    
    def test_remove_provider(self):
        """Test removing a provider"""
        # Add a provider first
        mock_provider = MagicMock()
        mock_provider.name = "mock_provider"
        mock_provider.is_available.return_value = True
        self.manager.add_provider(mock_provider, priority=0)
        
        self.assertIn("mock_provider", self.manager.providers)
        
        # Remove it
        self.manager.remove_provider("mock_provider")
        
        self.assertNotIn("mock_provider", self.manager.providers)
        self.assertNotIn("mock_provider", self.manager.provider_priority)
    
    def test_get_available_providers(self):
        """Test getting list of available providers"""
        # Replace the real provider with a mock that is available
        mock_provider = MagicMock()
        mock_provider.is_available.return_value = True
        self.manager.providers["yahoo_finance"] = mock_provider
        self.manager.provider_priority = ["yahoo_finance"]

        available = self.manager._get_available_providers()
        self.assertIn("yahoo_finance", available)
    
    @patch('backend.market_data.services.manager.YFinanceProvider')
    def test_get_quote_success(self, mock_yf_provider_class):
        """Test successful quote retrieval through manager"""
        # Setup mock provider
        mock_provider = MagicMock()
        mock_provider.is_available.return_value = True
        mock_provider.get_quote.return_value = Quote(
            symbol="AAPL",
            price=150.0,
            timestamp=datetime.now(),
            provider="yahoo_finance",
            data_status=DataStatus.DELAYED
        )
        
        # Replace the provider in manager
        self.manager.providers["yahoo_finance"] = mock_provider
        self.manager.provider_priority = ["yahoo_finance"]
        
        # Test
        quote = self.manager.get_quote("AAPL")
        
        # Assertions
        self.assertIsInstance(quote, Quote)
        self.assertEqual(quote.symbol, "AAPL")
        self.assertEqual(quote.price, 150.0)
        mock_provider.get_quote.assert_called_once_with("AAPL")
    
    @patch('backend.market_data.services.manager.YFinanceProvider')
    def test_get_quote_fallback(self, mock_yf_provider_class):
        """Test fallback when primary provider fails"""
        # Setup mock providers
        mock_primary = MagicMock()
        mock_primary.name = "yahoo_finance"
        mock_primary.is_available.return_value = True
        mock_primary.get_quote.side_effect = Exception("Primary provider failed")
        
        mock_fallback = MagicMock()
        mock_fallback.name = "fallback_provider"
        mock_fallback.is_available.return_value = True
        mock_fallback.get_quote.return_value = Quote(
            symbol="AAPL",
            price=149.5,
            timestamp=datetime.now(),
            provider="fallback_provider",
            data_status=DataStatus.DELAYED
        )
        
        # Replace providers in manager
        self.manager.providers = {
            "yahoo_finance": mock_primary,
            "fallback_provider": mock_fallback
        }
        self.manager.provider_priority = ["yahoo_finance", "fallback_provider"]
        
        # Test
        quote = self.manager.get_quote("AAPL")
        
        # Assertions
        self.assertIsInstance(quote, Quote)
        self.assertEqual(quote.symbol, "AAPL")
        self.assertEqual(quote.price, 149.5)  # From fallback provider
        self.assertEqual(quote.provider, "fallback_provider")
        mock_primary.get_quote.assert_called_once_with("AAPL")
        mock_fallback.get_quote.assert_called_once_with("AAPL")
    
    def test_get_provider_statuses(self):
        """Test getting status of all providers"""
        # Mock provider statuses
        with patch.object(self.manager.providers["yahoo_finance"], 'get_provider_status') as mock_status:
            mock_status.return_value = ProviderStatus(
                provider_name="yahoo_finance",
                is_healthy=True,
                timestamp=datetime.now()
            )
            
            statuses = self.manager.get_provider_statuses()
            
            self.assertIn("yahoo_finance", statuses)
            self.assertTrue(statuses["yahoo_finance"].is_healthy)
            mock_status.assert_called_once()

if __name__ == '__main__':
    unittest.main()
