"""
Tests for MarketDataManager
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../'))

from backend.market_data.services.manager import MarketDataManager
from backend.models.market_data import (
    Bar,
    DataStatus,
    ProviderStatus,
    Quote,
)


class TestMarketDataManager(unittest.TestCase):

    def setUp(self):
        self.manager = MarketDataManager()
        # Ensure the bar_repository module is loadable so patch() can find it
        # even though backend.repositories is a namespace package.
        import backend.repositories.bar_repository  # noqa: F401

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
        """Test fallback when primary provider fails on all retries."""
        # Setup mock providers — primary always raises, fallback succeeds.
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
        # Primary was retried 3 times before the manager fell through.
        self.assertEqual(mock_primary.get_quote.call_count, 3)
        mock_primary.get_quote.assert_called_with("AAPL")
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

    # ------------------------------------------------------------------
    # Phase 2 closure tests — stale data, retry/backoff, cache TTL.
    # ------------------------------------------------------------------

    def _make_bar(self, symbol: str, timeframe: str, ts: datetime) -> Bar:
        return Bar(
            symbol=symbol,
            timestamp=ts,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
            timeframe=timeframe,
            provider="yahoo_finance",
            data_status=DataStatus.LIVE,
        )

    def test_get_historical_bars_stale_returns_stale_status(self):
        """Intraday cache older than TTL is returned with DataStatus.STALE."""
        now = datetime.now()
        old_ts = now - timedelta(seconds=600)  # 10 min old — over default TTL (300s)
        # Need 312+ bars to pass the 80% count gate (390 expected for 1m/1d).
        cached = [self._make_bar("AAPL", "1m", old_ts) for _ in range(320)]
        mock_db = MagicMock()

        with patch(
            "backend.repositories.bar_repository"
        ) as mock_repo, \
             patch(
            "backend.market_data.services.manager._settings"
        ) as mock_settings:
            mock_repo.get_bars.return_value = cached
            mock_settings.market_data.cache_ttl_seconds = 300
            self.manager.providers = {
                "yahoo_finance": MagicMock(is_available=lambda: True)
            }
            self.manager.provider_priority = ["yahoo_finance"]

            result = self.manager.get_historical_bars(
                "AAPL", timeframe="1m", range_="1d", db=mock_db
            )

        self.assertEqual(len(result), 320)
        self.assertTrue(all(b.data_status == DataStatus.STALE for b in result))
        # Provider was NOT called — stale cache was returned as-is.
        self.manager.providers["yahoo_finance"].get_historical_bars.assert_not_called()

    def test_get_historical_bars_daily_skips_age_check(self):
        """Daily bars are not flagged stale even if newest is hours old."""
        now = datetime.now()
        old_ts = now - timedelta(hours=24)  # very old, but daily
        cached = [self._make_bar("AAPL", "1d", old_ts) for _ in range(80)]
        mock_db = MagicMock()

        with patch(
            "backend.repositories.bar_repository"
        ) as mock_repo, \
             patch(
            "backend.market_data.services.manager._settings"
        ) as mock_settings:
            mock_repo.get_bars.return_value = cached
            mock_settings.market_data.cache_ttl_seconds = 300
            self.manager.providers = {
                "yahoo_finance": MagicMock(is_available=lambda: True)
            }
            self.manager.provider_priority = ["yahoo_finance"]

            result = self.manager.get_historical_bars(
                "AAPL", timeframe="1d", range_="3mo", db=mock_db
            )

        # Daily bars: NOT tagged stale (age check is skipped for them).
        self.assertTrue(all(b.data_status == DataStatus.LIVE for b in result))

    def test_get_quote_retries_on_transient_failure(self):
        """A transient failure should be retried (3 attempts) before falling through."""
        mock_primary = MagicMock()
        mock_primary.name = "yahoo_finance"
        mock_primary.is_available.return_value = True
        # First two attempts fail, third succeeds.
        mock_primary.get_quote.side_effect = [
            Exception("transient 1"),
            Exception("transient 2"),
            Quote(
                symbol="AAPL", price=150.0, timestamp=datetime.now(),
                provider="yahoo_finance", data_status=DataStatus.DELAYED,
            ),
        ]

        self.manager.providers = {"yahoo_finance": mock_primary}
        self.manager.provider_priority = ["yahoo_finance"]

        quote = self.manager.get_quote("AAPL")

        self.assertEqual(quote.price, 150.0)
        # tenacity retried up to 3 times.
        self.assertEqual(mock_primary.get_quote.call_count, 3)

    def test_get_historical_bars_cache_ttl_ceiling(self):
        """When the intraday cache is fresher than TTL, it is used as-is (no provider call)."""
        now = datetime.now()
        fresh_ts = now - timedelta(seconds=10)  # well under TTL
        cached = [self._make_bar("AAPL", "5m", fresh_ts) for _ in range(80)]
        mock_db = MagicMock()

        with patch(
            "backend.repositories.bar_repository"
        ) as mock_repo, \
             patch(
            "backend.market_data.services.manager._settings"
        ) as mock_settings:
            mock_repo.get_bars.return_value = cached
            mock_settings.market_data.cache_ttl_seconds = 300
            self.manager.providers = {
                "yahoo_finance": MagicMock(is_available=lambda: True)
            }
            self.manager.provider_priority = ["yahoo_finance"]

            result = self.manager.get_historical_bars(
                "AAPL", timeframe="5m", range_="1d", db=mock_db
            )

        self.assertEqual(len(result), 80)
        # Not stale, not touched, no provider hit.
        self.assertTrue(all(b.data_status == DataStatus.LIVE for b in result))
        self.manager.providers["yahoo_finance"].get_historical_bars.assert_not_called()

if __name__ == '__main__':
    unittest.main()
