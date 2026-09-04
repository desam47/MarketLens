"""
Symbol history backfill service (Phase 3.7).

Provides ``backfill_symbol_history()`` which fills the bars table for a
symbol by downloading three tiers of data:

  **Tier 1 — 1m bars (last 30 days)**
    Alpaca primary via its native ``get_historical_bars()`` method.
    yfinance gap-fill for the ~15 min lag at the tip where Alpaca free
    tier is delayed.

  **Tier 2 — 1h bars (last ~730 days)**
    Alpaca primary; yfinance/webull fallback from BACKFILL_1H_* in .env.

  **Tier 3 — 1d bars (days 31 → bar_retention_days)**
    Alpaca primary; yfinance/webull fallback from BACKFILL_1D_* in .env.
    13:30 ET noise rows from Alpaca free tier are dropped.

Concurrency is controlled by a single ``asyncio.Semaphore(2)`` so at most
two symbols are backfilled concurrently. A per-symbol ``asyncio.Lock`` acts
as a single-flight guard: concurrent requests for the same symbol are queued
rather than triggering duplicate work.

The public entry point is ``backfill_symbol_history(symbol, days)``.
It runs synchronously (via ``asyncio.to_thread``) so it can be called from
any context including the synchronous FastAPI router layer.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from backend.config.settings import settings as _settings
from backend.database import SessionLocal
from backend.models import Bar
from backend.repositories.bar_repository import (
    bulk_delete_bars,
    prune_bars_older_than,
    upsert_bars,
)
from backend.market_data.services.manager_class import MarketDataManager

logger = logging.getLogger(__name__)


def _utc_key(b) -> datetime:
    """Phase 3.8.6 — sortable UTC key for any Bar timestamp.

    Providers return a mix of tz-aware UTC and naive NY datetimes; the
    sort key normalizes both to tz-aware UTC so mixed lists sort cleanly.
    """
    if b.timestamp.tzinfo is None:
        from backend.utils.timezone import ny_to_utc
        return ny_to_utc(b.timestamp)
    return b.timestamp.astimezone(timezone.utc)


# Phase 3.3.12: single-flight guard — one concurrent backfill per symbol.
_backfill_locks: dict[str, asyncio.Lock] = {}
_lock_guard = asyncio.Lock()  # guards _backfill_locks dict itself


async def _get_lock(symbol: str) -> asyncio.Lock:
    """Return the (possibly newly created) asyncio.Lock for ``symbol``."""
    async with _lock_guard:
        if symbol not in _backfill_locks:
            _backfill_locks[symbol] = asyncio.Lock()
        return _backfill_locks[symbol]


def _instantiate_provider(name: str):
    """Resolve a provider name (e.g. 'alpaca', 'webull', 'yahoo_finance') to an instance.

    Reads the global provider registry from ``manager._PROVIDER_CLASSES`` so
    that any provider registered at startup can be used by the backfill
    chain without a hardcoded import here. Returns None if the name is
    unknown or the class cannot be instantiated.
    """
    from backend.market_data.services.manager import _PROVIDER_CLASSES
    provider_cls = _PROVIDER_CLASSES.get(name)
    if provider_cls is None:
        logger.warning(
            f"Unknown backfill provider {name!r} — "
            f"available: {list(_PROVIDER_CLASSES.keys())}"
        )
        return None
    try:
        return provider_cls()
    except Exception as e:
        logger.warning(f"Failed to instantiate {name}: {e}")
        return None


# Phase 3.3.12: max 2 concurrent backfills to stay under provider rate limits.
_backfill_semaphore = asyncio.Semaphore(2)


# ---------------------------------------------------------------------------
# Tier 1 — 1m bars: Alpaca primary + yfinance gap-fill
# ---------------------------------------------------------------------------

async def _fetch_tier1_1m_bars(
    symbol: str,
    days: int,
    manager: MarketDataManager,
    db_session,
) -> list[Bar]:
    """Fetch 1m bars — primary + fallback + gap-fill for ~15 min lag.

    Strategy:
      1. Fetch via primary (BACKFILL_1M_PRIMARY, e.g. Alpaca, up to ~90 days
         via free tier).
      2. If primary returned nothing (auth failure, rate limit, etc.), walk
         the fallback chain (BACKFILL_1M_FALLBACK) to populate the merge
         dict. The first fallback that returns bars wins.
      3. Gap-fill: BACKFILL_1M_GAPFILL (default webull) fills the latest
         ~15 min window where Alpaca free tier is delayed.
      4. Deduplicate by timestamp and sort ascending.

    Providers are loaded from BACKFILL_1M_PRIMARY, BACKFILL_1M_FALLBACK,
    and BACKFILL_1M_GAPFILL in .env.
    """
    tf = "1m"
    merged: dict[datetime, Bar] = {}

    # Pick Alpaca range based on requested days.
    if days <= 1:
        alpaca_range = "1d"
    elif days <= 5:
        alpaca_range = "5d"
    elif days <= 30:
        alpaca_range = "1mo"
    else:
        alpaca_range = "3mo"  # free-tier cap

    # 1. Primary from .env (e.g. BACKFILL_1M_PRIMARY=alpaca).
    primary_returned = 0
    try:
        from backend.market_data.services.manager import get_backfill_primary_provider
        provider = get_backfill_primary_provider("1m")
        if provider is not None:
            bars = provider.get_historical_bars(
                symbol=symbol, timeframe=tf, range_=alpaca_range
            )
            for b in bars:
                merged[b.timestamp] = b
            primary_returned = len(bars)
            logger.debug(f"tier1 1m: {provider.__class__.__name__} returned {len(bars)} bars for {symbol}")
    except Exception as e:
        logger.warning(f"tier1 1m: primary provider failed for {symbol}: {e}")

    # 2. Fallback chain — only runs if primary returned zero bars.
    # Providers are loaded from BACKFILL_1M_FALLBACK in .env
    # (default: webull, yahoo_finance). First to return wins.
    if primary_returned == 0:
        from backend.market_data.services.manager import get_1m_fallback_providers
        for fb_name in get_1m_fallback_providers():
            try:
                prov = _instantiate_provider(fb_name)
                if prov is None:
                    continue
                bars = prov.get_historical_bars(
                    symbol=symbol, timeframe=tf, range_="5d"
                )
                for b in bars:
                    merged[b.timestamp] = b
                if bars:
                    logger.debug(
                        f"tier1 1m: {fb_name} fallback returned {len(bars)} bars for {symbol}"
                    )
                    break  # stop after first successful fallback
            except Exception as e:
                logger.debug(f"tier1 1m: {fb_name} fallback failed for {symbol}: {e}")
                continue

    # 3. Gap-fill: webull / yfinance for the latest ~15 min.
    # Provider names are loaded from BACKFILL_1M_GAPFILL in .env.
    from backend.market_data.services.manager import get_1m_gapfill_providers
    for gapfill_name in get_1m_gapfill_providers():
        try:
            prov = _instantiate_provider(gapfill_name)
            if prov is None:
                continue

            gap_bars = prov.get_historical_bars(
                symbol=symbol, timeframe=tf, range_="2d"
            )
            added = 0
            for b in gap_bars:
                if b.timestamp not in merged:
                    merged[b.timestamp] = b
                    added += 1
            if added:
                logger.debug(
                    f"tier1 1m: {gapfill_name} gapfill added {added} bars for {symbol}"
                )
            break  # stop after first successful gapfill
        except Exception as e:
            logger.debug(f"tier1 1m: {gapfill_name} gapfill failed for {symbol}: {e}")
            continue

    # Phase 3.8.6: normalize all timestamps to UTC-aware before sorting.
    # Alpaca returns tz-aware UTC; yfinance may return naive NY; both are
    # valid Bar timestamps but can't be compared directly without conversion.
    result = sorted(merged.values(), key=_utc_key)
    logger.debug(
        f"tier1 1m: total {len(result)} merged bars for {symbol} (last {days}d)"
    )
    return result


# ---------------------------------------------------------------------------
# Tier 2 — 1h bars: Alpaca primary + yfinance/webull fallback
# ---------------------------------------------------------------------------

async def _fetch_tier2_1h_bars(symbol: str, days: int) -> list[Bar]:
    """Fetch 1h bars — Alpaca primary, yfinance/webull fallback.

    Range is chosen based on ``days``:
      - days > 180  → "1y"
      - otherwise   → "6mo"
    """
    from backend.market_data.services.manager import get_1h_1d_fallback_providers

    if days > 180:
        range_str = "1y"
    else:
        range_str = "6mo"

    # Try primary provider from .env (BACKFILL_1H_PRIMARY=alpaca).
    try:
        from backend.market_data.services.manager import get_backfill_primary_provider
        provider = get_backfill_primary_provider("1h")
        if provider is not None:
            bars = provider.get_historical_bars(
                symbol=symbol, timeframe="1h", range_=range_str
            )
            # Phase 3.8.6: drop bars that don't align to full-hour boundaries.
            # Alpaca/Webull can return 1h bars at 30-minute offsets (e.g. 10:30
            # instead of 10:00). Only keep bars where minute == 0.
            bars = [b for b in bars if b.timestamp.minute == 0]
            bars.sort(key=_utc_key)
            logger.debug(f"tier2 1h: {provider.__class__.__name__} returned {len(bars)} bars for {symbol}")
            return bars
    except Exception as e:
        logger.warning(f"tier2 1h: primary provider failed for {symbol}: {e}")

    # Try fallback providers (BACKFILL_1H_FALLBACK in .env).
    for fb_name in get_1h_1d_fallback_providers("1h"):
        try:
            provider = _instantiate_provider(fb_name)
            if provider is None:
                continue

            bars = provider.get_historical_bars(
                symbol=symbol, timeframe="1h", range_=range_str
            )
            # Phase 3.8.6: keep only full-hour bars.
            bars = [b for b in bars if b.timestamp.minute == 0]
            bars.sort(key=_utc_key)
            logger.debug(f"tier2 1h: {fb_name} returned {len(bars)} bars for {symbol}")
            return bars
        except Exception as e:
            logger.debug(f"tier2 1h: {fb_name} fallback failed for {symbol}: {e}")
            continue

    return []


# ---------------------------------------------------------------------------
# Tier 3 — 1d bars: Alpaca primary + yfinance/webull fallback + 13:30 guard
# ---------------------------------------------------------------------------

async def _fetch_tier2_1d_bars(
    symbol: str,
    days_start: int,
) -> list[Bar]:
    """Fetch 1d bars — Alpaca primary, yfinance/webull fallback (Phase 3.7).

    ``days_start`` is the number of calendar days back from today to start fetching.
    Bars from ``days_start`` days ago up to today are returned (the 13:30 ET noise
    rows from Alpaca free tier are dropped).
    """
    from backend.market_data.services.manager import get_1h_1d_fallback_providers

    # Phase 3.8.6: keep the cutoff as timezone-aware UTC. Providers return
    # bars with tzinfo attached (UTC) and Python refuses to compare naive
    # and aware datetimes, so don't round-trip through to_ny here.
    start_cutoff_utc = datetime.now(timezone.utc) - timedelta(days=days_start)

    def _after_cutoff(b: Bar) -> bool:
        ts = b.timestamp
        if ts.tzinfo is None:
            # Naive NY-local bars from the provider layer; align to UTC for compare.
            from backend.utils.timezone import ny_to_utc
            ts = ny_to_utc(ts)
        return ts >= start_cutoff_utc

    # Try primary provider from .env (BACKFILL_1D_PRIMARY=alpaca).
    try:
        from backend.market_data.services.manager import get_backfill_primary_provider
        provider = get_backfill_primary_provider("1d")
        if provider is not None:
            bars: list[Bar] = provider.get_historical_bars(
                symbol=symbol, timeframe="1d", range_="5y"
            )
            # Filter to the requested window.
            bars = [b for b in bars if _after_cutoff(b)]
            # Drop 13:30 ET noise from Alpaca free tier.
            bars = [
                b for b in bars
                if not (b.timestamp.hour == 13 and b.timestamp.minute == 30)
            ]
            bars.sort(key=_utc_key)
            logger.debug(f"tier3 1d: {provider.__class__.__name__} returned {len(bars)} bars for {symbol}")
            return bars
    except Exception as e:
        logger.warning(f"tier3 1d: primary provider failed for {symbol}: {e}")

    # Try fallback providers (BACKFILL_1D_FALLBACK in .env).
    for fb_name in get_1h_1d_fallback_providers("1d"):
        try:
            provider = _instantiate_provider(fb_name)
            if provider is None:
                continue

            bars = provider.get_historical_bars(
                symbol=symbol, timeframe="1d", range_="2y"
            )
            bars = [b for b in bars if _after_cutoff(b)]
            bars.sort(key=_utc_key)
            logger.debug(f"tier3 1d: {fb_name} returned {len(bars)} bars for {symbol}")
            return bars
        except Exception as e:
            logger.debug(f"tier3 1d: {fb_name} fallback failed for {symbol}: {e}")
            continue

    return []


# ---------------------------------------------------------------------------
# Write helper
# ---------------------------------------------------------------------------

async def _write_bars_in_chunks(
    db_session,
    bars: list[Bar],
    chunk_size: int = 5000,
) -> int:
    """Write ``bars`` to the DB in chunks of ``chunk_size`` rows.

    Returns the total number of rows written.
    """
    total = 0
    for i in range(0, len(bars), chunk_size):
        chunk = bars[i : i + chunk_size]
        written = upsert_bars(db_session, chunk)
        total += written
        if len(bars) > chunk_size:
            await asyncio.sleep(0)
    return total


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def backfill_symbol_history(symbol: str, days: int | None = None) -> dict:
    """Backfill bar history for ``symbol`` covering ``days`` calendar days.

    Downloads three tiers:
      - Tier 1: 1m bars (last 30 days) — Alpaca primary + yfinance gap-fill
      - Tier 2: 1h bars (last min(730, days) days) — Alpaca primary + fallback
      - Tier 3: 1d bars (days 31 → ``days``) — Alpaca primary + fallback

    Concurrency: at most 2 symbols are backfilled simultaneously. Requests
    for the same symbol are queued (single-flight pattern).

    Returns:
        ``{"symbol", "tier1_written", "tier2_written", "tier3_written",
           "duration_s", "skipped"}``
    """
    settings = _settings
    retention_days = days if days is not None else settings.market_data.bar_retention_days

    # Hard floor so we never request zero or negative ranges.
    retention_days = max(1, retention_days)

    symbol = symbol.upper()
    start_time = time.monotonic()

    # Single-flight: wait for any in-progress backfill for this symbol.
    lock = await _get_lock(symbol)

    # Try to acquire without blocking — if the lock is already held, a
    # concurrent request for the same symbol is already in progress.
    acquired = lock.locked()
    if acquired:
        logger.info(
            f"backfill_symbol_history: {symbol} already being backfilled — skipping"
        )
        return {
            "symbol": symbol,
            "tier1_written": 0,
            "tier2_written": 0,
            "tier3_written": 0,
            "duration_s": time.monotonic() - start_time,
            "skipped": True,
        }

    async with lock:
        logger.info(
            f"backfill_symbol_history: starting backfill for {symbol} "
            f"({retention_days}d)"
        )

        # Use the semaphore to limit total concurrent backfills to 2.
        async with _backfill_semaphore:
            db = SessionLocal()
            try:
                manager = MarketDataManager()
                tier1_written = 0
                tier2_written = 0
                tier3_written = 0

                # Tier 1: 1m bars (Alpaca primary + yfinance gap-fill).
                tier1_days = min(30, retention_days)
                tier1_bars = await _fetch_tier1_1m_bars(
                    symbol, tier1_days, manager, db
                )
                if tier1_bars:
                    tier1_written = await _write_bars_in_chunks(db, tier1_bars)

                # Tier 2: 1h bars — up to ~1 year.
                tier2_days = min(365, retention_days)
                tier2_bars = await _fetch_tier2_1h_bars(symbol, tier2_days)
                if tier2_bars:
                    tier2_written = await _write_bars_in_chunks(db, tier2_bars)

                # Tier 3: 1d bars (last retention_days of daily data).
                # 3-year default covers ~750 trading days of 1d history.
                if retention_days > 0:
                    tier3_bars = await _fetch_tier2_1d_bars(
                        symbol,
                        days_start=retention_days,
                    )
                    if tier3_bars:
                        tier3_written = await _write_bars_in_chunks(db, tier3_bars)

                duration = time.monotonic() - start_time
                logger.info(
                    f"backfill_symbol_history: {symbol} done in {duration:.1f}s — "
                    f"tier1(1m)={tier1_written}, tier2(1h)={tier2_written}, "
                    f"tier3(1d)={tier3_written}"
                )
                return {
                    "symbol": symbol,
                    "tier1_written": tier1_written,
                    "tier2_written": tier2_written,
                    "tier3_written": tier3_written,
                    "duration_s": round(duration, 2),
                    "skipped": False,
                }
            finally:
                db.close()


def backfill_symbol_history_sync(symbol: str, days: int | None = None) -> dict:
    """Synchronous wrapper around ``backfill_symbol_history()``.

    Use this from non-async contexts such as FastAPI route handlers,
    startup hooks, or CLI scripts.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — create one.
        return asyncio.run(backfill_symbol_history(symbol, days))

    # Running loop: schedule on it.
    future = asyncio.run_coroutine_threadsafe(
        backfill_symbol_history(symbol, days), loop
    )
    return future.result()
