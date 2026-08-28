"""
Phase 18 — Abstract base classes for auxiliary data providers.

Three independent provider types, each following the same pattern as the
Phase 2 MarketDataProvider ABC:

  NewsProvider        — headlines, source, timestamp, relevance
  FundamentalProvider — market cap, revenue, EPS, P/E, debt, cash, ownership
  OptionsProvider     — calls, puts, volume, IV, Greeks, OI, put/call ratio

They are intentionally separate from the core trend engine and never
called by it. Each is disabled by default (AUX_*_ENABLED=false).
"""
from abc import ABC, abstractmethod
from datetime import datetime

from backend.models.aux_data import (
    AuxProviderStatus,
    FundamentalsResponse,
    NewsResponse,
    OptionsResponse,
)


class NewsProvider(ABC):
    """Abstract base class for news data providers."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._is_healthy = True
        self._last_error: str | None = None

    @abstractmethod
    def get_news(self, symbol: str, limit: int = 20) -> NewsResponse:
        """Return recent news items for ``symbol``, newest first."""

    def get_status(self) -> AuxProviderStatus:
        return AuxProviderStatus(
            provider_name=self.name,
            provider_type="news",
            is_healthy=self._is_healthy,
            last_error=self._last_error,
            last_success=datetime.utcnow() if self._is_healthy else None,
            timestamp=datetime.utcnow(),
        )

    def _mark_error(self, exc: Exception) -> None:
        self._is_healthy = False
        self._last_error = str(exc)

    def _mark_ok(self) -> None:
        self._is_healthy = True
        self._last_error = None


class FundamentalProvider(ABC):
    """Abstract base class for fundamental data providers."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._is_healthy = True
        self._last_error: str | None = None

    @abstractmethod
    def get_fundamentals(self, symbol: str) -> FundamentalsResponse:
        """Return a fundamental snapshot for ``symbol``."""

    def get_status(self) -> AuxProviderStatus:
        return AuxProviderStatus(
            provider_name=self.name,
            provider_type="fundamentals",
            is_healthy=self._is_healthy,
            last_error=self._last_error,
            last_success=datetime.utcnow() if self._is_healthy else None,
            timestamp=datetime.utcnow(),
        )

    def _mark_error(self, exc: Exception) -> None:
        self._is_healthy = False
        self._last_error = str(exc)

    def _mark_ok(self) -> None:
        self._is_healthy = True
        self._last_error = None


class OptionsProvider(ABC):
    """Abstract base class for options data providers."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._is_healthy = True
        self._last_error: str | None = None

    @abstractmethod
    def get_options(self, symbol: str, expiration: str | None = None) -> OptionsResponse:
        """Return the options chain for ``symbol``.

        If ``expiration`` is supplied, return only that expiration date.
        Otherwise return all available expirations (up to provider limits).
        """

    def get_status(self) -> AuxProviderStatus:
        return AuxProviderStatus(
            provider_name=self.name,
            provider_type="options",
            is_healthy=self._is_healthy,
            last_error=self._last_error,
            last_success=datetime.utcnow() if self._is_healthy else None,
            timestamp=datetime.utcnow(),
        )

    def _mark_error(self, exc: Exception) -> None:
        self._is_healthy = False
        self._last_error = str(exc)

    def _mark_ok(self) -> None:
        self._is_healthy = True
        self._last_error = None
