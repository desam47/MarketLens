"""
Market data provider interface and base classes
"""
import logging
import traceback
from abc import ABC, abstractmethod
from datetime import datetime

from ..models.market_data import (
    Bar,
    MarketStatus,
    ProviderCapabilities,
    ProviderStatus,
    Quote,
)

logger = logging.getLogger(__name__)


class MarketDataProvider(ABC):
    """Abstract base class for market data providers"""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """Get latest quote for a symbol"""

    @abstractmethod
    def get_bar(self, symbol: str, timeframe: str, timestamp: datetime) -> Bar:
        """Get historical bar for a symbol at specific timeframe and timestamp"""

    @abstractmethod
    def get_latest_bar(self, symbol: str, timeframe: str) -> Bar:
        """Get most recent bar for a symbol and timeframe"""

    @abstractmethod
    def get_historical_bars(
        self,
        symbol: str,
        timeframe: str = "1d",
        range_: str = "3mo",
        include_extended_hours: bool = False,
    ) -> list[Bar]:
        """Get a series of historical bars for a symbol.

        The provider is expected to honor the ``timeframe`` and ``range_``
        request as best it can; implementations may map the values to
        whatever the upstream API accepts. An empty list is returned when
        the provider has no data (for example, a delisted symbol).

        ``include_extended_hours``: when True and ``timeframe == "1m"``,
        also fetch pre-market/after-hours bars (in addition to regular
        trading hours). Providers that don't support this accept and ignore
        the flag — RTH-only is always a valid (if incomplete) answer, so
        this must never raise. Only WebullProvider currently honors it
        (confirmed live 2026-09-09: Webull's ``trading_sessions`` param
        supports PRE/RTH/ATH). Returned bars carry their real
        ``Bar.session`` classification either way.
        """

    @abstractmethod
    def get_batch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Get quotes for multiple symbols"""

    @abstractmethod
    def get_market_status(self, symbol: str) -> MarketStatus:
        """Get market status for a symbol"""

    @abstractmethod
    def get_provider_status(self) -> ProviderStatus:
        """Get provider health/status"""

    @abstractmethod
    def get_capabilities(self) -> ProviderCapabilities:
        """Get provider capabilities"""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if provider is available/healthy"""

class BaseMarketDataProvider(MarketDataProvider):
    """Base implementation with common functionality"""

    def __init__(self, name: str):
        super().__init__(name)
        self._last_error: str | None = None
        self._is_healthy = True

    def _handle_error(self, error: Exception, context: str = ""):
        """Handle and record provider errors"""
        self._last_error = f"{context}: {error!s}" if context else str(error)
        self._is_healthy = False
        logger.error(
            "Provider %s error in %s: %s",
            self.name,
            context,
            error,
            exc_info=True,
        )
        raise error

    def _reset_error_state(self):
        """Reset error state after successful operation"""
        self._last_error = None
        self._is_healthy = True

    def get_provider_status(self) -> ProviderStatus:
        """Get provider health/status"""
        from datetime import datetime
        return ProviderStatus(
            provider_name=self.name,
            is_healthy=self._is_healthy,
            latency_ms=None,  # Would be measured in real implementation
            rate_limit_remaining=None,
            last_success=datetime.now() if self._is_healthy else None,
            error_message=self._last_error,
            timestamp=datetime.now()
        )

    def is_available(self) -> bool:
        """Check if provider is available/healthy"""
        return self._is_healthy
