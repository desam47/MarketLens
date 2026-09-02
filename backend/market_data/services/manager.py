"""
Backward-compatibility shim.

The market data services package was split into focused submodules:
  - ``cache``         — Redis caching layer
  - ``providers``      — provider registry, rate limiter, circuit breaker
  - ``manager_class`` — ``MarketDataManager`` class

Tests and external callers have historically imported from this module
(``backend.market_data.services.manager``); this shim re-exports the
public API so those imports keep working without modification.

IMPORTANT: import _settings and redis from _providers so that patches at
``backend.market_data.services.manager._settings`` and
``backend.market_data.services.manager.redis`` propagate to all internal modules.
"""
from . import _providers as _shared
from ._providers import get_redis, get_redis_cache, get_settings, redis
from .cache import RedisCache, _redis_cache
from .manager_class import MarketDataManager, market_data_manager
# Re-export the module-level logger so tests that patch
# ``backend.market_data.services.manager.logger`` find it.
from .providers import logger
from .providers import (
    _EXPECTED_BAR_COUNTS,
    _INTRADAY_TIMEFRAMES,
    _PROVIDER_CLASSES,
    _PerProviderRateLimiter,
    _call_provider,
    _cb_lock,
    _circuit_breakers,
    _corr_id_fn,
    _correlation_id_placeholder,
    _get_breaker,
    _get_finnhub_class,
    _get_per_provider_rate_limit,
    _get_webull_class,
    _get_alpaca_class,
    _newest_bar_age_seconds,
    _provider_call_with_breaker,
    _provider_retry,
    _rate_limiter,
    _register_finnhub,
    _register_alpaca,
)
from backend.market_data.providers.yfinance_provider import YFinanceProvider

# Re-export the names tests patch — these must be the SAME objects as
# _providers._settings and _providers.redis so that test patches work.
# ``_settings`` and ``redis`` are bound directly from _providers so they
# share the same object identity.
_settings = _shared._settings

__all__ = [
    # Classes
    "MarketDataManager",
    "RedisCache",
    "market_data_manager",
    "_redis_cache",
    "_rate_limiter",
    "_PerProviderRateLimiter",
    # Registry
    "_PROVIDER_CLASSES",
    # Circuit breaker registry
    "_circuit_breakers",
    "_cb_lock",
    "_get_breaker",
    # Call wrapper
    "_call_provider",
    "_provider_call_with_breaker",
    "_provider_retry",
    "_get_per_provider_rate_limit",
    "_correlation_id_placeholder",
    "_corr_id_fn",
    # Constants / helpers
    "_INTRADAY_TIMEFRAMES",
    "_EXPECTED_BAR_COUNTS",
    "_newest_bar_age_seconds",
    # Lazy resolvers
    "_get_webull_class",
    "_get_finnhub_class",
    "_get_alpaca_class",
    "_register_finnhub",
    "_register_alpaca",
    # For test patches
    "_settings",
    "redis",
    "YFinanceProvider",
]
