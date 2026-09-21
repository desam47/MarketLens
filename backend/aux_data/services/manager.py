"""
AuxDataManager — Phase 18 — orchestrates news/fundamentals/options providers.

Each provider type has its own independent priority chain and is enabled/
disabled via settings. The manager is stateless between calls (no caching);
each request hits the provider directly. This keeps the implementation
minimal while still honouring rate limits and error handling.

The manager mirrors the ``MarketDataManager`` pattern from Phase 2:
  - primary + fallback providers per category
  - first-healthy wins
  - graceful degradation (empty response + status, never raises)
"""

import logging
import time
from typing import Literal

from backend.config.settings import settings as _settings
from backend.models.aux_data import (
    AuxProviderStatus,
    FundamentalsResponse,
    NewsResponse,
    OptionsResponse,
)
from backend.utils.timezone import now_ny

from ..provider import FundamentalProvider, NewsProvider, OptionsProvider
from ..providers import (
    FinnhubNewsProvider,
    WebullFundamentalsProvider,
    YFinanceFundamentalsProvider,
    YFinanceNewsProvider,
    YFinanceOptionsProvider,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry — one entry per provider class (extend to add new backends).
# ---------------------------------------------------------------------------
_PROVIDER_CLASSES: dict[str, type] = {
    "yfinance_news": YFinanceNewsProvider,
    "yfinance_fundamentals": YFinanceFundamentalsProvider,
    "yfinance_options": YFinanceOptionsProvider,
    "finnhub_news": FinnhubNewsProvider,
    "webull_fundamentals": WebullFundamentalsProvider,
}

# ---------------------------------------------------------------------------
# Rate limiter (minimal, per-category)
# ---------------------------------------------------------------------------
_category_rate_limiter: dict[str, float] = {}
_category_last_call: dict[str, float] = {}


def _enforce_rate_limit(category: str) -> None:
    """Simple per-category throttle: wait until ``rate_limit_per_minute`` allows."""
    limit = _settings.aux_data
    cfg = (
        limit.news
        if category == "news"
        else limit.fundamentals
        if category == "fundamentals"
        else limit.options
    )
    rate = cfg.rate_limit_per_minute if cfg.enabled else 0
    if rate <= 0:
        return

    min_interval = 60.0 / rate
    now = time.monotonic()
    last = _category_last_call.get(category, 0.0)
    wait = min_interval - (now - last)
    if wait > 0:
        time.sleep(wait)
    _category_last_call[category] = time.monotonic()


# ---------------------------------------------------------------------------
# Per-category manager stubs
# ---------------------------------------------------------------------------
class _CategoryManager:
    """Stateless, rate-limited first-healthy-wins dispatcher."""

    def __init__(
        self,
        category: Literal["news", "fundamentals", "options"],
        provider_type: type,
        settings_group: str,  # "news" | "fundamentals" | "options"
    ) -> None:
        self.category = category
        self.provider_type = provider_type
        self.settings_group: str = settings_group
        self._providers: list = []
        self._init_providers()

    def _init_providers(self) -> None:
        cfg = getattr(_settings.aux_data, self.settings_group)
        ordered = [cfg.primary_provider] + [
            f for f in cfg.fallback_providers if f != cfg.primary_provider
        ]
        for name in ordered:
            cls = _PROVIDER_CLASSES.get(name)
            if cls is None:
                logger.warning(
                    "Provider '%s' for %s not registered — skipping. Known: %s",
                    name,
                    self.category,
                    list(_PROVIDER_CLASSES),
                )
                continue
            try:
                self._providers.append(cls())
            except Exception as exc:
                logger.warning(
                    "Failed to instantiate %s provider '%s': %s",
                    self.category,
                    name,
                    exc,
                )

    def _get_config(self):
        return getattr(_settings.aux_data, self.settings_group)

    def is_enabled(self) -> bool:
        return self._get_config().enabled

    def get_statuses(self) -> list[AuxProviderStatus]:
        return [p.get_status() for p in self._providers]

    def _dispatch(self, method_name: str, *args, **kwargs):
        """Call ``method_name`` on the first healthy provider; return empty on all failure."""
        _enforce_rate_limit(self.category)
        last_exc: Exception | None = None
        for prov in self._providers:
            try:
                method = getattr(prov, method_name)
                return method(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "%s.%s failed for %s: %s",
                    prov.name,
                    method_name,
                    args,
                    exc,
                )
                continue
        # All providers failed — return an empty response
        logger.error("All %s providers failed; last error: %s", self.category, last_exc)
        return self._empty_response(args[0] if args else "")

    def _empty_response(self, symbol: str):
        if self.provider_type is NewsProvider:
            return NewsResponse(
                symbol=symbol.upper(), items=[], provider="none", timestamp=now_ny()
            )
        if self.provider_type is FundamentalProvider:
            from backend.models.aux_data import FundamentalsItem

            return FundamentalsResponse(
                symbol=symbol.upper(),
                data=FundamentalsItem(symbol=symbol.upper()),
                provider="none",
                timestamp=now_ny(),
            )
        return OptionsResponse(symbol=symbol.upper(), provider="none", timestamp=now_ny())


class AuxDataManager:
    """Facade exposing the three category managers with a single interface."""

    def __init__(self) -> None:
        self.news = _CategoryManager("news", NewsProvider, "news")
        self.fundamentals = _CategoryManager("fundamentals", FundamentalProvider, "fundamentals")
        self.options = _CategoryManager("options", OptionsProvider, "options")

    def get_news(self, symbol: str, limit: int = 20) -> NewsResponse:
        if not self.news.is_enabled():
            logger.info("News provider is disabled (AUX_NEWS_ENABLED=false)")
            return NewsResponse(
                symbol=symbol.upper(), items=[], provider="disabled", timestamp=now_ny()
            )
        return self.news._dispatch("get_news", symbol, limit)

    def get_fundamentals(self, symbol: str) -> FundamentalsResponse:
        if not self.fundamentals.is_enabled():
            logger.info("Fundamentals provider is disabled (AUX_FUNDAMENTALS_ENABLED=false)")
            from backend.models.aux_data import FundamentalsItem

            return FundamentalsResponse(
                symbol=symbol.upper(),
                data=FundamentalsItem(symbol=symbol.upper()),
                provider="disabled",
                timestamp=now_ny(),
            )
        return self.fundamentals._dispatch("get_fundamentals", symbol)

    def get_options(self, symbol: str, expiration: str | None = None) -> OptionsResponse:
        if not self.options.is_enabled():
            logger.info("Options provider is disabled (AUX_OPTIONS_ENABLED=false)")
            return OptionsResponse(symbol=symbol.upper(), provider="disabled", timestamp=now_ny())
        return self.options._dispatch("get_options", symbol, expiration)

    def get_all_statuses(self) -> dict[str, list[AuxProviderStatus]]:
        return {
            "news": self.news.get_statuses(),
            "fundamentals": self.fundamentals.get_statuses(),
            "options": self.options.get_statuses(),
        }


# Singleton
aux_data_manager = AuxDataManager()
