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

    Providers return a mix of:
      - tz-aware UTC (Alpaca),
      - naive NY (yfinance, Webull, resampled bars),
      - tz-aware NY (rare).
    The sort key normalizes all to tz-aware UTC so mixed lists sort cleanly.
    """
    ts = b.timestamp
    if ts.tzinfo is None:
        # Naive timestamp — project convention is NY local.
        from backend.utils.timezone import ny_to_utc
        return ny_to_utc(ts)
    # tz-aware: convert to UTC regardless of original zone.
    return ts.astimezone(timezone.utc)


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
    elif days <= 15:
        alpaca_range = "15d"  # Phase 3.9: 15 trading days
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
            logger.info(f"tier1 1m: {provider.__class__.__name__} returned {len(bars)} bars for {symbol} (range_={alpaca_range})")
    except Exception as e:
        logger.warning(f"tier1 1m: primary provider failed for {symbol}: {e}")

    # 2. Fallback chain — only runs if primary returned zero bars.
    # Providers are loaded from BACKFILL_1M_FALLBACK in .env
    # (default: webull, yahoo_finance). First to return wins.
    # Phase 3.9: use the same window as the primary so Webull's paginator
    # can produce up to ~5,850 bars across 4 pages.
    if primary_returned == 0:
        from backend.market_data.services.manager import get_1m_fallback_providers
        for fb_name in get_1m_fallback_providers():
            try:
                prov = _instantiate_provider(fb_name)
                if prov is None:
                    continue
                bars = prov.get_historical_bars(
                    symbol=symbol, timeframe=tf, range_=alpaca_range
                )
                for b in bars:
                    merged[b.timestamp] = b
                if bars:
                    logger.debug(
                        f"tier1 1m: {fb_name} fallback returned {len(bars)} bars for {symbol}"
                    )
                    break  # stop after first successful fallback
            except Exception as e:
                logger.info(f"tier1 1m: {fb_name} fallback failed for {symbol}: {e}")
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
# Tier 2 — 1h bars: primary + gap-fill merge (env-driven chain)
# ---------------------------------------------------------------------------

async def _fetch_tier2_1h_bars(symbol: str, days: int) -> list[Bar]:
    """Fetch 1h bars — primary + gap-fill (env-driven).

    Webull's M60 endpoint returns 1h bars at clock-hour :00 offsets (9:00,
    10:00…) but caps at 1,200 bars and omits the 16:00 close bar. Alpaca
    and yfinance fill those gaps without pulling 5 years of overlapping history.

    Logic:
      1. Fetch primary provider (BACKFILL_1H_PRIMARY) — Webull by default.
         This is the authoritative source covering the most recent ~1,200 bars.
      2. Build the set of timestamps returned by primary.
      3. For each gap-fill provider (BACKFILL_1H_FALLBACK), fetch bars for
         the same range but SKIP any bar whose (symbol, timeframe, timestamp)
         key already exists in the primary set. Only genuinely new timestamps
         from the fallback are included.
      4. Result = primary bars + only-new fallback bars. No overlapping history
         from fallbacks is pulled.

    The fallback chain is fully driven by BACKFILL_1H_PRIMARY and
    BACKFILL_1H_FALLBACK in .env — no hardcoded provider names here.
    """
    from backend.market_data.services.manager import get_1h_1d_fallback_providers

    range_str = "5y"
    from backend.market_data.services.ingestion_service import _normalize_1h_bar

    def _class_name(p) -> str:
        return p.__class__.__name__

    def _short_name(name: str) -> str:
        return name.removesuffix("Provider").lower()

    primary_bars: list[Bar] = []
    primary_provider_name: str | None = None

    # Step 1: Fetch primary provider.
    try:
        from backend.market_data.services.manager import get_backfill_primary_provider
        provider = get_backfill_primary_provider("1h")
        if provider is not None:
            primary_provider_name = _class_name(provider)
            bars = provider.get_historical_bars(
                symbol=symbol, timeframe="1h", range_=range_str
            )
            normalized = [
                n for n in (_normalize_1h_bar(b, primary_provider_name) for b in bars)
                if n is not None
            ]
            primary_bars.extend(normalized)
            logger.debug(
                f"tier2 1h: {primary_provider_name} (primary, {range_str}) "
                f"returned {len(normalized)} bars for {symbol}"
            )
    except Exception as e:
        logger.warning(f"tier2 1h: primary provider failed for {symbol}: {e}")

    # Build the set of primary timestamps so we can skip overlapping fallbacks.
    primary_keys: set[tuple] = {
        (b.symbol, b.timeframe, b.timestamp) for b in primary_bars
    }

    # Step 2: For each gap-fill provider, only include bars NOT in primary.
    # NOTE: use the SAME range_str as the primary so gap-fill providers don't
    # return 5 years of overlapping history (e.g. Alpaca "5y" would add bars
    # from 2021 that the primary doesn't cover). The gap-fill providers should
    # only add bars within the primary's date range — extra timestamps outside
    # that range are excluded since the user only wants the primary's history
    # plus the missing bars (e.g. 16:00 close) within it.
    if not primary_bars:
        logger.debug("tier2 1h: no primary bars — skipping gap-fill")
        return []

    primary_min = min(b.timestamp for b in primary_bars)
    primary_max = max(b.timestamp for b in primary_bars)

    # Allow gap-fill providers to contribute bars up to 1 hour PAST primary_max
    # so the 16:00 ET close bar (which Webull omits) is not filtered out.
    # Webull's last bar of the session opens at 15:00 ET, making primary_max
    # 15:00 ET; the 16:00 ET close bar sits 1 hour later and is valid.
    primary_max_extended = primary_max + timedelta(hours=1)

    # Per-provider range cap: yahoo_finance 1h endpoint only supports ≤730d.
    _1H_RANGE_CAPS: dict[str, str] = {
        "yahoo_finance": "730d",
    }

    fallback_bars: list[Bar] = []
    for fb_name in get_1h_1d_fallback_providers("1h"):
        try:
            provider = _instantiate_provider(fb_name)
            if provider is None:
                continue
            fb_class_name = _class_name(provider)
            fb_range = _1H_RANGE_CAPS.get(fb_name, range_str)
            bars = provider.get_historical_bars(
                symbol=symbol, timeframe="1h", range_=fb_range
            )
            # Normalize to :00, then keep only bars whose keys are NOT in
            # primary AND whose timestamp falls within the primary's date range
            # (extended by 1h to capture the 16:00 ET close bar).
            new_bars = []
            for b in bars:
                n = _normalize_1h_bar(b, fb_class_name)
                if n is None:
                    continue
                key = (n.symbol, n.timeframe, n.timestamp)
                if key in primary_keys:
                    continue
                # Only include bars within primary's date range (+ 1h buffer).
                if n.timestamp < primary_min or n.timestamp > primary_max_extended:
                    continue
                new_bars.append(n)
            fallback_bars.extend(new_bars)
            if new_bars:
                logger.info(
                    f"tier2 1h: {fb_class_name} (gap-fill) added "
                    f"{len(new_bars)} bars for {symbol} — "
                    f"16:00 / pre-market fills within primary range "
                    f"({primary_min.strftime('%Y-%m-%d')} → {primary_max.strftime('%Y-%m-%d')})"
                )
            elif len(bars) > 0:
                logger.debug(
                    f"tier2 1h: {fb_class_name} (gap-fill) returned {len(bars)} bars "
                    f"but all were already covered by primary — skipping"
                )
        except Exception as e:
            logger.debug(f"tier2 1h: {fb_class_name} gap-fill failed for {symbol}: {e}")
            continue

    # Step 3: Combine and return.
    all_bars = primary_bars + fallback_bars
    if not all_bars:
        return []
    all_bars.sort(key=_utc_key)
    return all_bars


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

    Downloads three tiers, each window configurable via .env (falls back to
    the hardcoded default shown, always capped by ``min(tier_days, days)``):
      - Tier 1: 1m bars — BACKFILL_1M_DAYS (default 15d) — primary + gap-fill
      - Tier 2: 1h bars — BACKFILL_1H_DAYS (default 365d) — primary + fallback
      - Tier 3: 1d bars — BACKFILL_1D_DAYS (default 1095d) — primary + fallback

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
                # Phase 3.9: extend to 15 trading days by default (now
                # configurable via BACKFILL_1M_DAYS). WebullProvider paginates
                # internally (4 pages × 1,650 = 6,600 bars max) when range_="15d"
                # is passed to get_historical_bars, so primary providers can now
                # cover the full 15-day window in one call.
                tier1_days = min(settings.backfill.tf_1m_days, retention_days)
                tier1_bars = await _fetch_tier1_1m_bars(
                    symbol, tier1_days, manager, db
                )
                if tier1_bars:
                    tier1_written = await _write_bars_in_chunks(db, tier1_bars)

                # Auto-resample sub-hour timeframes from 1m (lazy import to avoid
                # circular dependency).
                # Always resample sub-hour TFs from 1m — upsert is idempotent so
                # re-running when tier1_written=0 (re-backfill of existing bars) is safe
                # and ensures 2m/3m/5m/15m/30m bars are populated.
                from backend.market_data.services.ingestion_service import ingestion_service
                try:
                    for tf in ingestion_service._SUBHOUR_TFS:
                        # full_history=True: this is a one-time pass right after
                        # backfill wrote (potentially years of) 1m history — resample
                        # all of it, not just the live loop's narrow recent-window
                        # default, so 2m/3m/5m/15m/30m get full historical depth
                        # instead of only whatever accumulates going forward.
                        written_sub = await ingestion_service._resample_and_upsert(
                            tf, source_tf="1m", _symbol=symbol, full_history=True
                        )
                        if written_sub > 0:
                            logger.info(f"backfill {symbol}: auto-resampled {written_sub} {tf} bars from 1m")
                except Exception as e:
                    logger.warning(f"backfill {symbol}: sub-hour resample failed: {e}")

                # Tier 2: 1h bars — range_="5y" always (hits 1,200-bar cap).
                # Window configurable via BACKFILL_1H_DAYS (default 365).
                tier2_days = min(settings.backfill.tf_1h_days, retention_days)
                tier2_bars = await _fetch_tier2_1h_bars(symbol, tier2_days)
                if tier2_bars:
                    tier2_written = await _write_bars_in_chunks(db, tier2_bars)

                # Tier 3: 1d bars. Window configurable via BACKFILL_1D_DAYS
                # (default 1095 ≈ 3 years), still capped by retention_days.
                tier3_days = min(settings.backfill.tf_1d_days, retention_days)
                if tier3_days > 0:
                    tier3_bars = await _fetch_tier2_1d_bars(
                        symbol,
                        days_start=tier3_days,
                    )
                    if tier3_bars:
                        tier3_written = await _write_bars_in_chunks(db, tier3_bars)

                # Auto-resample higher timeframes from what was just written.
                if tier2_written > 0:
                    try:
                        written_4h = await ingestion_service._resample_1h_to_4h_and_upsert(_symbol=symbol)
                        logger.info(f"backfill {symbol}: auto-resampled {written_4h} 4h bars from 1h")
                    except Exception as e:
                        logger.warning(f"backfill {symbol}: 4h resample failed: {e}")

                if tier3_written > 0:
                    try:
                        written_1wk = await ingestion_service._resample_1d_to_1wk_and_upsert(_symbol=symbol)
                        logger.info(f"backfill {symbol}: auto-resampled {written_1wk} 1wk bars from 1d")
                    except Exception as e:
                        logger.warning(f"backfill {symbol}: 1wk resample failed: {e}")

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
