"""
MarketDataManager - handles provider selection, fallback, caching, etc.
"""
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from backend.models.market_data import Bar, MarketStatus, ProviderStatus, Quote

from ..provider import MarketDataProvider
from ..providers.yfinance_provider import YFinanceProvider

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Approximate bar counts to expect for a given (timeframe, range) tuple.
# Used to decide whether the cache is "good enough" to return without
# hitting a provider. Values are deliberately generous; if a cache returns
# at least 80% of the expected count, treat it as sufficient.
_EXPECTED_BAR_COUNTS: dict[tuple[str, str], int] = {
    ("1d", "1mo"): 22,
    ("1d", "3mo"): 65,
    ("1d", "6mo"): 130,
    ("1d", "1y"): 252,
    ("1d", "2y"): 504,
    ("1d", "5y"): 1260,
    ("1h", "1mo"): 200,
    ("1h", "3mo"): 600,
    ("1h", "1y"): 1900,
    ("5m", "1d"): 80,
    ("5m", "5d"): 400,
    ("15m", "5d"): 130,
    ("30m", "5d"): 65,
    ("1m", "1d"): 390,
    ("1m", "5d"): 1950,
    ("1wk", "2y"): 104,
    ("1wk", "5y"): 260,
    ("1mo", "5y"): 60,
}


class MarketDataManager:
    """Manages market data providers with fallback and caching"""

    def __init__(self):
        self.providers: dict[str, MarketDataProvider] = {}
        self.provider_priority: list[str] = []  # List of provider names in priority order
        self.provider_priorities: dict[str, int] = {}  # Map of provider_name -> priority
        self._initialize_providers()

    def _initialize_providers(self):
        """Initialize available providers"""
        # Add Yahoo Finance as the initial free provider
        yf_provider = YFinanceProvider()
        self.add_provider(yf_provider, priority=0)

        # In future phases, other providers would be added here
        # e.g., Alpha Vantage, IEX Cloud, Webull (disabled by default)

    def add_provider(self, provider: MarketDataProvider, priority: int = 0):
        """Add a provider to the manager"""
        self.providers[provider.name] = provider
        # Insert provider into priority list based on priority value
        self.provider_priorities[provider.name] = priority
        # Re-sort provider_priority based on priorities
        self.provider_priority = sorted(self.provider_priorities.keys(), key=lambda x: self.provider_priorities[x])

        logger.info(f"Added provider '{provider.name}' with priority {priority}")

    def remove_provider(self, provider_name: str):
        """Remove a provider from the manager"""
        if provider_name in self.providers:
            del self.providers[provider_name]
            if provider_name in self.provider_priorities:
                del self.provider_priorities[provider_name]
                # Re-sort the provider_priority list
                self.provider_priority = sorted(self.provider_priorities.keys(), key=lambda x: self.provider_priorities[x])
            logger.info(f"Removed provider '{provider_name}'")

    def _get_available_providers(self) -> list[str]:
        """Get list of available providers in priority order"""
        available = []
        for provider_name in self.provider_priority:
            provider = self.providers.get(provider_name)
            if provider and provider.is_available():
                available.append(provider_name)
        return available

    def get_quote(self, symbol: str) -> Quote:
        """Get quote for a symbol with fallback"""
        last_error = None

        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                quote = provider.get_quote(symbol)
                logger.debug(f"Got quote for {symbol} from {provider_name}")
                return quote
            except Exception as e:
                last_error = e
                logger.warning(f"Failed to get quote for {symbol} from {provider_name}: {e}")
                continue

        # If all providers failed, raise the last error
        if last_error:
            raise last_error
        else:
            raise RuntimeError("No available providers")

    def get_bar(self, symbol: str, timeframe: str, timestamp: datetime) -> Bar:
        """Get historical bar for a symbol with fallback"""
        last_error = None

        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                bar = provider.get_bar(symbol, timeframe, timestamp)
                logger.debug(f"Got bar for {symbol} from {provider_name}")
                return bar
            except Exception as e:
                last_error = e
                logger.warning(f"Failed to get bar for {symbol} from {provider_name}: {e}")
                continue

        if last_error:
            raise last_error
        else:
            raise RuntimeError("No available providers")

    def get_latest_bar(self, symbol: str, timeframe: str) -> Bar:
        """Get latest bar for a symbol with fallback"""
        last_error = None

        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                bar = provider.get_latest_bar(symbol, timeframe)
                logger.debug(f"Got latest bar for {symbol} from {provider_name}")
                return bar
            except Exception as e:
                last_error = e
                logger.warning(f"Failed to get latest bar for {symbol} from {provider_name}: {e}")
                continue

        if last_error:
            raise last_error
        else:
            raise RuntimeError("No available providers")

    def get_historical_bars(
        self,
        symbol: str,
        timeframe: str = "1d",
        range_: str = "3mo",
        use_cache: bool = True,
        db: "Session | None" = None,
    ) -> list[Bar]:
        """Get a series of historical bars for a symbol, with optional caching.

        When ``use_cache`` is True and a DB session is provided, the manager
        will first look in the local ``bars`` table and return those rows if
        the cache covers at least 80% of the expected bar count for the
        requested ``(timeframe, range_)`` tuple. Otherwise it falls through
        to the primary provider, persists the result, and returns it.

        Never raises; returns ``[]`` on failure so callers (e.g. the
        scanner) can treat "no history" as a benign state.
        """
        expected = _EXPECTED_BAR_COUNTS.get((timeframe, range_), 0)
        cache_threshold = int(expected * 0.8) if expected else 0

        if use_cache and db is not None:
            try:
                # Local import keeps the manager importable without
                # requiring SQLAlchemy to be configured at import time.
                from backend.repositories import bar_repository

                cached = bar_repository.get_bars(
                    db, symbol=symbol, timeframe=timeframe, limit=None
                )
                if cached and (
                    cache_threshold == 0 or len(cached) >= cache_threshold
                ):
                    logger.debug(
                        f"Cache hit for {symbol} {timeframe} {range_}: "
                        f"{len(cached)} bars (threshold {cache_threshold})"
                    )
                    return cached
            except Exception as e:
                logger.warning(f"Bar cache lookup failed for {symbol}: {e}")

        # Cache miss (or cache disabled / unavailable) — fetch from provider.
        bars: list[Bar] = []
        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                bars = provider.get_historical_bars(
                    symbol, timeframe=timeframe, range_=range_
                )
                logger.debug(
                    f"Got {len(bars)} historical bars for {symbol} "
                    f"({timeframe}, {range_}) from {provider_name}"
                )
                break
            except Exception as e:
                logger.warning(
                    f"Failed to get historical bars for {symbol} from "
                    f"{provider_name}: {e}"
                )
                continue

        if not bars:
            return []

        if db is not None:
            try:
                from backend.repositories import bar_repository
                written = bar_repository.upsert_bars(db, bars)
                logger.debug(f"Persisted {written} bars for {symbol} {timeframe}")
            except Exception as e:
                logger.warning(
                    f"Failed to persist bars for {symbol} {timeframe}: {e}"
                )

        return bars

    def get_batch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Get quotes for multiple symbols with fallback"""
        # For batch quotes, we'll try to use providers that support it efficiently
        # For now, we'll fall back to individual quotes
        quotes = {}
        errors = []

        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                batch_quotes = provider.get_batch_quotes(symbols)
                # Only use the result if we got quotes for all requested symbols
                if all(symbol in batch_quotes and batch_quotes[symbol].data_status != "ERROR"
                       for symbol in symbols):
                    quotes = batch_quotes
                    logger.debug(f"Got batch quotes for {symbols} from {provider_name}")
                    break
                else:
                    # Partial success, but we'll try other providers for complete data
                    logger.warning(f"Partial batch quote success from {provider_name}")
            except Exception as e:
                errors.append(f"{provider_name}: {e}")
                logger.warning(f"Failed to get batch quotes for {symbols} from {provider_name}: {e}")
                continue

        # If we didn't get complete data from any provider, fall back to individual quotes
        if not all(symbol in quotes and quotes[symbol].data_status != "ERROR" for symbol in symbols):
            logger.info("Falling back to individual quote requests")
            quotes = {}
            for symbol in symbols:
                try:
                    quotes[symbol] = self.get_quote(symbol)
                except Exception as e:
                    logger.error(f"Failed to get quote for {symbol}: {e}")
                    quotes[symbol] = Quote(
                        symbol=symbol.upper(),
                        price=0.0,
                        timestamp=datetime.now(),
                        provider="fallback_failed",
                        data_status="ERROR"
                    )

        return quotes

    def get_market_status(self, symbol: str) -> MarketStatus:
        """Get market status for a symbol with fallback"""
        last_error = None

        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                status = provider.get_market_status(symbol)
                logger.debug(f"Got market status for {symbol} from {provider_name}")
                return status
            except Exception as e:
                last_error = e
                logger.warning(f"Failed to get market status for {symbol} from {provider_name}: {e}")
                continue

        if last_error:
            raise last_error
        else:
            raise RuntimeError("No available providers")

    def get_provider_statuses(self) -> dict[str, ProviderStatus]:
        """Get status of all providers"""
        statuses = {}
        for name, provider in self.providers.items():
            try:
                statuses[name] = provider.get_provider_status()
            except Exception as e:
                logger.error(f"Failed to get status for provider {name}: {e}")
                statuses[name] = ProviderStatus(
                    provider_name=name,
                    is_healthy=False,
                    error_message=str(e),
                    timestamp=datetime.now()
                )
        return statuses

# Global instance
market_data_manager = MarketDataManager()
