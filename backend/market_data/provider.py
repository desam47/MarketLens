"""
Market data provider interface and base classes
"""
from abc import ABC, abstractmethod
from datetime import datetime

from ..models.market_data import (
    Bar,
    MarketStatus,
    ProviderCapabilities,
    ProviderStatus,
    Quote,
)


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
    ) -> list[Bar]:
        """Get a series of historical bars for a symbol.

        The provider is expected to honor the ``timeframe`` and ``range_``
        request as best it can; implementations may map the values to
        whatever the upstream API accepts. An empty list is returned when
        the provider has no data (for example, a delisted symbol).
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
        # In a real implementation, you might want to log this
        import traceback
        print(f"ERROR in {self.name}.{context}: {error}")
        print(traceback.format_exc())
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
