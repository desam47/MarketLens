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

import threading

from backend.market_data.providers.yfinance_provider import YFinanceProvider

from . import _providers as _shared
from ._providers import redis
from .cache import RedisCache, _redis_cache
from .manager_class import MarketDataManager, market_data_manager

# Re-export the module-level logger so tests that patch
# ``backend.market_data.services.manager.logger`` find it.
from .providers import (
    _EXPECTED_BAR_COUNTS,
    _INTRADAY_TIMEFRAMES,
    _PROVIDER_CLASSES,
    _call_provider,
    _cb_lock,
    _circuit_breakers,
    _corr_id_fn,
    _correlation_id_placeholder,
    _get_alpaca_class,
    _get_breaker,
    _get_finnhub_class,
    _get_per_provider_rate_limit,
    _get_webull_class,
    _newest_bar_age_seconds,
    _PerProviderRateLimiter,
    _provider_call_with_breaker,
    _provider_retry,
    _rate_limiter,
    _register_alpaca,
    _register_finnhub,
    logger,
)

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


# Phase: 2026-09-09 — process-lifetime provider instance cache.
#
# This and backfill_service._instantiate_provider used to call
# provider_cls() fresh on EVERY invocation — unlike MarketDataManager,
# which instantiates each provider once in its own __init__ and reuses
# them for the rest of the process. For WebullProvider specifically,
# __init__ does a synchronous auth handshake
# (TradeClient(...).account_v2.get_account_list()) — a real blocking
# network round-trip on every construction, not just the first.
#
# Found live 2026-09-09: this repeated re-instantiation — happening
# inside _gapfill_1m_loop/_gapfill_1h_loop, which run on the SAME
# asyncio event loop as the live 1m ingestion tick, both inside
# MarketDataIngestionService's concurrent loops — was blocking that
# shared event loop for hundreds of ms to seconds per gap-fill cycle,
# well beyond _bar_ingestion_loop's own ~60s cadence. Confirmed via
# _ingest_1m_recent_window() timing 1.67s in isolation, yet the live
# logs showed ~100s between consecutive "Ingested" lines — the
# difference was fresh WebullProvider() bootstraps (each with its own
# "Webull SDK configured..." → token verify/refresh sequence) firing
# from the concurrently-running gap-fill loop in between. That's the
# direct cause of bars landing 1-2+ minutes late instead of ~60s.
# Caching by provider name for the life of the process (matching
# MarketDataManager's own provider cache lifetime) fixes this:
# construct once, reuse forever. Only successful constructions are
# cached — a transient failure is never cached as "permanently
# unavailable", so the next call retries construction from scratch.
_provider_instance_cache: dict[str, object] = {}

# Single-flight construction lock, one per provider name. Without this,
# concurrent callers for the same not-yet-cached name (e.g.
# warmup_tape_engines() spawning one seed thread per watchlist symbol,
# each hitting get_cached_provider("webull")) would all race past the
# `name in _provider_instance_cache` check together and each construct
# their own instance. For WebullProvider that meant one auth handshake
# (account_v2.get_account_list()) per racing caller — confirmed live
# 2026-09-16: a ~17-symbol watchlist warmup fired ~17 simultaneous
# bootstrap calls and tripped Webull's own rate limiter (429
# TOO_MANY_REQUESTS), which then kept re-triggering since a failed
# construction is deliberately left uncached (see below) so the next
# caller retries. Serializing construction per name collapses that
# burst to one real attempt; the rest wait and reuse its result.
_provider_construction_locks: dict[str, threading.Lock] = {}
_provider_locks_guard = threading.Lock()


def _construction_lock(name: str) -> threading.Lock:
    with _provider_locks_guard:
        lock = _provider_construction_locks.get(name)
        if lock is None:
            lock = threading.Lock()
            _provider_construction_locks[name] = lock
        return lock


def get_cached_provider(name: str):
    """Return a cached instance of the named provider, constructing it
    once and reusing it for the life of the process. Returns ``None``
    (never raises) if the name is unknown or construction fails —
    failures are never cached, so the next call retries.
    """
    if _settings.startup_mode == "api":
        logger.info("Provider %r is unavailable while STARTUP_MODE=api", name)
        return None
    if name in _provider_instance_cache:
        return _provider_instance_cache[name]
    provider_cls = _PROVIDER_CLASSES.get(name)
    if provider_cls is None:
        logger.warning(f"Unknown provider {name!r} — available: {list(_PROVIDER_CLASSES.keys())}")
        return None
    with _construction_lock(name):
        # Re-check: another thread may have constructed (or failed to
        # construct) this provider while we were waiting for the lock.
        if name in _provider_instance_cache:
            return _provider_instance_cache[name]
        # MarketDataManager builds its own provider set independently at
        # startup (MarketDataManager._initialize_providers, a separate
        # code path from this cache). If it already has a live instance
        # for `name`, adopt it instead of constructing a second one —
        # confirmed live 2026-09-16: that startup construction and this
        # cache's first caller (e.g. tape warmup) raced within the same
        # second, each running its own Webull auth handshake, adding to
        # the request burst this lock is otherwise collapsing.
        existing = market_data_manager.providers.get(name)
        if existing is not None:
            _provider_instance_cache[name] = existing
            return existing
        try:
            instance = provider_cls()
        except Exception as e:
            logger.warning(f"Failed to instantiate {name}: {e}")
            return None
        _provider_instance_cache[name] = instance
        return instance


def _clear_provider_cache() -> None:
    """Test-only: reset the cache so a test's mocked _PROVIDER_CLASSES
    isn't shadowed by another test's cached instance."""
    _provider_instance_cache.clear()


def get_backfill_primary_provider(timeframe: str) -> MarketDataManager | None:
    """Return the (cached) primary backfill provider for ``timeframe``, from .env.

    The provider name is read from BACKFILL_{TF}_PRIMARY (e.g. BACKFILL_1M_PRIMARY).
    The provider class is resolved from the global registry (``_PROVIDER_CLASSES``)
    so any provider in the registry can be used as primary without code changes.
    Returns None if the provider name is unknown or the class cannot be imported.
    """
    from backend.config.settings import settings as _s

    primary_name = _s.backfill.get_primary_provider(timeframe)
    return get_cached_provider(primary_name)


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
