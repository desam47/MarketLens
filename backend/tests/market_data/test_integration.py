"""
Integration tests for market data system
"""
import os
import sys
import unittest

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from backend.market_data.services.manager import market_data_manager


class TestMarketDataIntegration(unittest.TestCase):
    
    def test_manager_initialization(self):
        """Test that the global market data manager is properly initialized"""
        self.assertIsNotNone(market_data_manager)
        self.assertTrue(hasattr(market_data_manager, 'providers'))
        self.assertTrue(hasattr(market_data_manager, 'get_quote'))
        self.assertTrue(hasattr(market_data_manager, 'get_latest_bar'))
    
    def test_provider_exists(self):
        """Test that Yahoo Finance provider is registered"""
        self.assertIn("yahoo_finance", market_data_manager.providers)
        provider = market_data_manager.providers["yahoo_finance"]
        self.assertIsNotNone(provider)
        self.assertEqual(provider.name, "yahoo_finance")

if __name__ == '__main__':
    unittest.main()
