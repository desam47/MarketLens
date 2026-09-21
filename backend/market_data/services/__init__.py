"""
Market data services — provider orchestration, caching, and fallback.

The package is split into focused submodules:

  - ``cache``   — Redis caching layer (``RedisCache``)
  - ``providers`` — provider registry, rate limiter, circuit breaker, call wrapper
  - ``manager_class`` — ``MarketDataManager`` class and singleton

This ``__init__`` re-exports the public API so existing call sites
(``from backend.market_data.services.manager import ...``) keep working unchanged.
"""
from backend.market_data.providers.yfinance_provider import YFinanceProvider

from ._providers import _settings, get_redis, get_redis_cache, get_settings, redis
from .cache import RedisCache, _redis_cache
from .manager_class import MarketDataManager, market_data_manager
from .providers import (
    _EXPECTED_BAR_COUNTS,
    # Helpers
    _INTRADAY_TIMEFRAMES,
    # Registry
    _PROVIDER_CLASSES,
    _call_provider,
    _cb_lock,
    _circuit_breakers,
    _corr_id_fn,
    _correlation_id_placeholder,
    _get_breaker,
    _get_finnhub_class,
    _get_per_provider_rate_limit,
    # Lazy provider resolvers
    _get_webull_class,
    _newest_bar_age_seconds,
    # Rate limiter
    _PerProviderRateLimiter,
    _provider_call_with_breaker,
    # Retry / circuit breaking
    _provider_retry,
    _rate_limiter,
    _register_finnhub,
)

__all__ = [
    # Classes
    "MarketDataManager",
    "RedisCache",
    "YFinanceProvider",
    # Singletons
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
    "_corr_id_fn",
    "_correlation_id_placeholder",
    # Constants / helpers
    "_INTRADAY_TIMEFRAMES",
    "_EXPECTED_BAR_COUNTS",
    "_newest_bar_age_seconds",
    # Lazy resolvers
    "_get_webull_class",
    "_get_finnhub_class",
    "_register_finnhub",
    # Test-patchable infrastructure
    "_settings",
    "redis",
    "get_settings",
    "get_redis",
    "get_redis_cache",
]
