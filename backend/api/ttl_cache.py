"""
In-process TTL cache for hot API endpoints (v2.1 Item 1.2).

This module is the *server-side* cache — separate from
``backend.api.cache`` (which is the HTTP caching middleware that emits
ETag/304 headers).

Why a separate module: the existing ``backend/api/cache.py`` handles
HTTP-level concerns (ETag, Cache-Control, 304 responses). This module
short-circuits before any work happens in the route handler, so a
cached request costs one dict lookup instead of:
  - re-fetching from yfinance (scanner)
  - re-running the regime/trend engine
  - re-reading the database (quotes)

Both layers complement each other: the HTTP middleware is for the
browser, the TTL cache is for the server.

Usage::

    from cachetools import TTLCache
    from backend.api.ttl_cache import ttl_cached

    _scan_cache: TTLCache[str, dict] = TTLCache(maxsize=200, ttl=10)

    @ttl_cached(_scan_cache, key_fn=lambda s: s.upper())
    async def _cached_scan(symbol: str):
        ...

    @router.get("/scanner/{symbol}")
    async def scan_symbol(symbol: str):
        return await _cached_scan(symbol)
"""
from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Any, Callable, ParamSpec, TypeVar

from cachetools import TTLCache

logger = logging.getLogger(__name__)

T = TypeVar("T")
P = ParamSpec("P")


# --- Pre-built caches -----------------------------------------------
# maxsize caps memory; ttl caps staleness.
# Sized to cover a typical session's worth of symbols (~10-50 active in
# a watchlist + a few ad-hoc lookups). Beyond maxsize, the oldest entry
# inside its TTL is evicted (LRU).
#
# Keyed by symbol (upper-cased) for scanner/regime/quote; by
# "symbol:timeframe" for trend.

# Scanner: 10s — indicators/quote don't shift faster than a dashboard
# refresh, and YFinance calls are expensive.
_scan_cache: TTLCache[str, Any] = TTLCache(maxsize=200, ttl=10)

# Regime: 30s — regime is a slow-moving classification.
_regime_cache: TTLCache[str, Any] = TTLCache(maxsize=200, ttl=30)

# Trend: 30s — trend signals change on a per-bar timescale, but the
# response only changes when the engine transitions.
_trend_cache: TTLCache[str, Any] = TTLCache(maxsize=200, ttl=30)

# Quote: 5s — most volatile; even a 5s window catches ~50% of
# back-to-back dashboard refreshes.
_quote_cache: TTLCache[str, Any] = TTLCache(maxsize=500, ttl=5)

# ── Phase 3.6.3: additional named caches for the remaining hot endpoints ──────
# Each cache covers a single route or small family that previously had no
# server-side memoisation. The HTTP cache middleware in ``backend.api.cache``
# still handles 304s on the browser side; these caches short-circuit the
# Python work entirely.

# Confluence: 30s — multi-timeframe alignment changes slowly and depends
# on all per-TF engines being warm.
_confluence_cache: TTLCache[str, Any] = TTLCache(maxsize=200, ttl=30)

# Strategy selector: 30s — strategy is a function of regime/trend/confluence,
# so once those settle, the chosen strategy only changes on transitions.
_strategy_cache: TTLCache[str, Any] = TTLCache(maxsize=200, ttl=30)

# Sector classification: 5 min — sector is a static-mapping lookup that
# never changes intra-session, but we keep a TTL so manual edits to the
# underlying mapping table are picked up without a restart.
_sector_cache: TTLCache[str, Any] = TTLCache(maxsize=200, ttl=300)

# Relative strength batch: 60s — RS scores are computed over a rolling
# window; 60s matches the dashboard refresh cadence and is short enough
# to stay in sync with active quotes.
_rs_batch_cache: TTLCache[str, Any] = TTLCache(maxsize=50, ttl=60)

# Market-context aggregate: 10s — context is the most-queried endpoint
# from the dashboard top bar; 10s is the dashboard's own refresh interval.
_context_cache: TTLCache[str, Any] = TTLCache(maxsize=20, ttl=10)

# Regime / trend / strategy history: 60s — historical endpoint payloads
# are immutable for a given (symbol, limit) pair, but a short TTL keeps
# the in-process size in check and lets a manual ``POST /update`` propagate.
_regime_history_cache: TTLCache[str, Any] = TTLCache(maxsize=200, ttl=60)
_trend_history_cache: TTLCache[str, Any] = TTLCache(maxsize=200, ttl=60)
_strategy_history_cache: TTLCache[str, Any] = TTLCache(maxsize=200, ttl=60)
_mtf_history_cache: TTLCache[str, Any] = TTLCache(maxsize=200, ttl=60)

# Analysis transitions: 30s — transitions change only when a regime or
# trend engine fires; matches the trend cache TTL.
_transitions_cache: TTLCache[str, Any] = TTLCache(maxsize=200, ttl=30)


def get_cache_stats() -> dict[str, dict[str, int]]:
    """Return current cache sizes for the health/monitoring endpoint."""
    return {
        "scanner": {"size": _scan_cache.currsize, "maxsize": _scan_cache.maxsize},
        "regime": {"size": _regime_cache.currsize, "maxsize": _regime_cache.maxsize},
        "trend": {"size": _trend_cache.currsize, "maxsize": _trend_cache.maxsize},
        "quote": {"size": _quote_cache.currsize, "maxsize": _quote_cache.maxsize},
        "confluence": {"size": _confluence_cache.currsize, "maxsize": _confluence_cache.maxsize},
        "strategy": {"size": _strategy_cache.currsize, "maxsize": _strategy_cache.maxsize},
        "sector": {"size": _sector_cache.currsize, "maxsize": _sector_cache.maxsize},
        "rs_batch": {"size": _rs_batch_cache.currsize, "maxsize": _rs_batch_cache.maxsize},
        "context": {"size": _context_cache.currsize, "maxsize": _context_cache.maxsize},
        "regime_history": {"size": _regime_history_cache.currsize, "maxsize": _regime_history_cache.maxsize},
        "trend_history": {"size": _trend_history_cache.currsize, "maxsize": _trend_history_cache.maxsize},
        "strategy_history": {"size": _strategy_history_cache.currsize, "maxsize": _strategy_history_cache.maxsize},
        "mtf_history": {"size": _mtf_history_cache.currsize, "maxsize": _mtf_history_cache.maxsize},
        "transitions": {"size": _transitions_cache.currsize, "maxsize": _transitions_cache.maxsize},
    }


def ttl_cached(cache: TTLCache, key_fn: Callable[..., str]):
    """Decorator that caches the result of an async or sync callable.

    ``key_fn`` derives the cache key from the call args. The decorator
    supports both ``async def`` and ``def`` functions transparently.

    A single ``KeyError`` from a missing key triggers re-computation;
    if the wrapped function raises, the error propagates and the cache
    is left untouched for that key.
    """
    def decorator(fn: Callable[P, T]) -> Callable[P, T]:
        @wraps(fn)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            key = key_fn(*args, **kwargs)
            if key in cache:
                return cache[key]
            # ``asyncio.iscoroutinefunction`` would be more precise, but
            # we only wrap async functions at the route layer, so the
            # coroutine check is implicit.
            if asyncio.iscoroutinefunction(fn):
                result = await fn(*args, **kwargs)
            else:
                result = await asyncio.to_thread(fn, *args, **kwargs)
            cache[key] = result
            return result

        @wraps(fn)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            key = key_fn(*args, **kwargs)
            if key in cache:
                return cache[key]
            result = fn(*args, **kwargs)
            cache[key] = result
            return result

        if asyncio.iscoroutinefunction(fn):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper  # type: ignore[return-value]
    return decorator
