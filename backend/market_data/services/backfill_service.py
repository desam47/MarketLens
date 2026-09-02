"""
Symbol history backfill service (Phase 3.3.12).

Provides ``backfill_symbol_history()`` which fills the bars table for a
symbol by downloading two tiers of data:

  **Tier 1 — 1m bars (last 30 days)**
    Uses the primary market-data provider (Finnhub / Yahoo Finance / etc.)
    via ``MarketDataManager.get_historical_bars()``.
    At ~390 1m bars per trading day × 22 trading days ≈ 8 580 rows.

  **Tier 2 — 1d bars (days 31 → bar_retention_days)**
    Uses the Alpaca provider via its native ``get_historical_bars()`` method.
    1d bars are cheap (≈ 1 row/symbol/day) and give the historical regime
    engine enough context to work correctly even when 1m data only covers
    the last 30 days.

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

# Phase 3.3.12: single-flight guard — one concurrent backfill per symbol.
_backfill_locks: dict[str, asyncio.Lock] = {}
_lock_guard = asyncio.Lock()  # guards _backfill_locks dict itself


async def _get_lock(symbol: str) -> asyncio.Lock:
    """Return the (possibly newly created) asyncio.Lock for ``symbol``."""
    async with _lock_guard:
        if symbol not in _backfill_locks:
            _backfill_locks[symbol] = asyncio.Lock()
        return _backfill_locks[symbol]


# Phase 3.3.12: max 2 concurrent backfills to stay under provider rate limits.
_backfill_semaphore = asyncio.Semaphore(2)


async def _fetch_tier1_1m_bars(
    symbol: str,
    days: int,
    manager: MarketDataManager,
    db_session,
) -> list[Bar]:
    """Fetch 1m bars for ``symbol`` covering the last ``days`` calendar days.

    Returns bars sorted ascending by timestamp. The primary/fallback provider
    chain is used via ``manager.get_historical_bars()``.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    tf = "1m"
    try:
        bars: list[Bar] = manager.get_historical_bars(
            symbol=symbol,
            timeframe=tf,
            range_=None,
            use_cache=False,
            db=db_session,
        )
        # Filter to the requested window (manager may widen the range).
        bars = [b for b in bars if start <= b.timestamp <= end]
        bars.sort(key=lambda b: b.timestamp)
        logger.debug(
            f"tier1: got {len(bars)} 1m bars for {symbol} "
            f"(last {days}d)"
        )
        return bars
    except Exception as e:
        logger.warning(f"tier1: failed to fetch 1m bars for {symbol}: {e}")
        return []


async def _fetch_tier2_1d_bars(
    symbol: str,
    days_start: int,
    days_end: int,
) -> list[Bar]:
    """Fetch 1d bars for ``symbol`` covering days ``days_start`` → ``days_end``.

    ``days_start`` is the number of days back from now to start fetching.
    ``days_end`` is the number of days back from now to stop fetching.
    E.g. days_start=1000, days_end=31 fetches bars from 1000 days ago to
    31 days ago (inclusive).

    Uses the Alpaca provider's ``get_historical_bars()`` with ``range_``.
    We request "5y" and filter to the window the caller needs, because
    Alpaca's range strings ("3mo", "1y", etc.) don't align cleanly with
    arbitrary day boundaries.
    """
    # The Alpaca free tier provides ~1000 days of IEX data. Use 5y range
    # as the maximum safe bucket; the results are filtered below.
    now = datetime.now(timezone.utc)
    start_cutoff = now - timedelta(days=days_start)
    end_cutoff = now - timedelta(days=max(1, days_end))

    try:
        # Lazily import so the module is loadable even if Alpaca deps are absent.
        from backend.market_data.providers.alpaca_provider import AlpacaProvider

        provider = AlpacaProvider()
        bars: list[Bar] = provider.get_historical_bars(
            symbol=symbol,
            timeframe="1d",
            range_="5y",  # Alpaca free tier ≈ 1000 IEX days
        )
        # Filter to the requested window [start_cutoff, end_cutoff].
        bars = [b for b in bars if start_cutoff <= b.timestamp <= end_cutoff]
        bars.sort(key=lambda b: b.timestamp)
        logger.debug(
            f"tier2: got {len(bars)} 1d bars for {symbol} "
            f"(days {days_start}→{days_end})"
        )
        return bars
    except Exception as e:
        logger.warning(f"tier2: Alpaca failed for {symbol} ({days_start}d→{days_end}d): {e}")
        return []


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


async def backfill_symbol_history(symbol: str, days: int | None = None) -> dict:
    """Backfill bar history for ``symbol`` covering ``days`` calendar days.

    Downloads Tier 1 (1m, last 30 days) and Tier 2 (1d, days 31 → ``days``)
    bars and writes them to the database in chunks of 5 000 rows.

    Concurrency: at most 2 symbols are backfilled simultaneously. Requests
    for the same symbol are queued (single-flight pattern).

    Arguments:
        symbol: uppercase ticker, e.g. ``"AAPL"``.
        days: number of calendar days of history to backfill. Defaults to
            ``settings.market_data.bar_retention_days``.

    Returns a summary dict:
        ``{"symbol", "tier1_written", "tier2_written", "duration_s",
           "skipped"}``
        ``skipped`` is ``True`` when a backfill for this symbol was already
        in progress (the single-flight guard kicked in).
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

                # Tier 1: 1m bars — last 30 days (or full window if < 30d).
                tier1_days = min(30, retention_days)
                tier1_bars = await _fetch_tier1_1m_bars(
                    symbol, tier1_days, manager, db
                )
                if tier1_bars:
                    tier1_written = await _write_bars_in_chunks(db, tier1_bars)

                # Tier 2: 1d bars — days 31 → retention_days.
                if retention_days > 30:
                    tier2_bars = await _fetch_tier2_1d_bars(
                        symbol,
                        days_start=retention_days,
                        days_end=30,
                    )
                    if tier2_bars:
                        tier2_written = await _write_bars_in_chunks(db, tier2_bars)

                duration = time.monotonic() - start_time
                logger.info(
                    f"backfill_symbol_history: {symbol} done in {duration:.1f}s — "
                    f"tier1={tier1_written}, tier2={tier2_written}"
                )
                return {
                    "symbol": symbol,
                    "tier1_written": tier1_written,
                    "tier2_written": tier2_written,
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
