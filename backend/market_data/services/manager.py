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

# Phase 3.7: provider chain helpers used by backfill_service.py.
# These resolve provider names from settings at call time so .env changes
# take effect without restarting the process.


def get_1m_gapfill_providers() -> list[str]:
    """1m gap-fill providers — fills the ~15 min lag window where Alpaca lags."""
    from backend.config.settings import settings as _s
    return _s.backfill.get_1m_gapfill_providers()


def get_1m_fallback_providers() -> list[str]:
    """1m fallback providers (after primary Alpaca fails)."""
    from backend.config.settings import settings as _s
    return _s.backfill.get_1m_fallback_providers()


def get_1h_1d_fallback_providers(timeframe: str) -> list[str]:
    """Fallback provider names for 1h or 1d backfill from .env."""
    from backend.config.settings import settings as _s
    return _s.backfill.get_fallback_providers(timeframe)


def get_backfill_primary_provider(timeframe: str) -> MarketDataManager | None:
    """Instantiate the primary backfill provider for ``timeframe`` from .env.

    The provider name is read from BACKFILL_{TF}_PRIMARY (e.g. BACKFILL_1M_PRIMARY).
    The provider class is resolved from the global registry (``_PROVIDER_CLASSES``)
    so any provider in the registry can be used as primary without code changes.
    Returns None if the provider name is unknown or the class cannot be imported.
    """
    from backend.config.settings import settings as _s

    primary_name = _s.backfill.get_primary_provider(timeframe)
    provider_cls = _PROVIDER_CLASSES.get(primary_name)
    if provider_cls is None:
        logger.warning(
            f"Unknown backfill primary provider {primary_name!r} for {timeframe} — "
            f"available: {list(_PROVIDER_CLASSES.keys())}"
        )
        return None
    try:
        return provider_cls()
    except Exception as e:
        logger.warning(f"Failed to instantiate backfill primary {primary_name}: {e}")
        return None


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
    # Phase 3.7 provider chain helpers
    "get_1m_gapfill_providers",
    "get_1m_fallback_providers",
    "get_1h_1d_fallback_providers",
]
