"""
Symbol history backfill service (Phase 3.7; rebuilt onto a single RQ job
— see module note below).

Provides ``backfill_symbol_history()`` which fills the bars table for a
symbol by downloading three tiers of data, gap-checking each, and only
then resampling:

  **Tier 1 — 1m bars (last BACKFILL_1M_DAYS, default 15 days)**
    Alpaca primary via its native ``get_historical_bars()`` method.
    yfinance gap-fill for the ~15 min lag at the tip where Alpaca free
    tier is delayed. Followed by a gap-check-and-fill pass (see
    ``_check_and_fill_gaps`` below) before 2m/3m/5m/15m/30m are resampled
    from it — resampling ungapped 1m data is the whole point of checking.

  **Tier 2 — 1h bars (last BACKFILL_1H_DAYS, default 365 days)**
    Alpaca primary; yfinance/webull fallback from BACKFILL_1H_* in .env.
    Same gap-check-and-fill pass before 4h is resampled from it.

  **Tier 3 — 1d bars (last BACKFILL_1D_DAYS, default 1095 days)**
    Alpaca primary; yfinance/webull fallback from BACKFILL_1D_* in .env.
    13:30 ET noise rows from Alpaca free tier are dropped. Same
    gap-check-and-fill pass before 1wk is resampled from it.

Single-flight + the 2-concurrent-symbols cap used to be enforced by a
module-level ``asyncio.Lock``/``asyncio.Semaphore`` — bare asyncio
primitives bind to whichever event loop first uses them, and this
function used to have two independent, uncoordinated callers (the
watchlist router's own trigger, plus ingestion_service's bootstrap)
running on two different event loops in the same process, which crashed
outright on first contention ("bound to a different event loop") until
the locks were re-keyed per loop as a stopgap.

That root cause — two racing callers — is gone as of the RQ-based
pipeline in ``backend/market_data/services/backfill_queue.py``: there is
now exactly ONE call site for this function, the RQ task
``backfill_symbol_task`` below, running in its own worker process with no
other event loop to race against. Single-flight is enforced upstream, by
``enqueue_backfill``'s DB+RQ check, before a job is ever enqueued — so
this module no longer needs (and no longer has) any lock/semaphore of its
own. ``settings.background.backfill_queue_name`` workers (run 2 of them
to reproduce the old concurrency cap) provide the "at most N concurrent"
property for free, at the process level.

The public entry point is ``backfill_symbol_history(symbol, days)``, an
async function called from ``backfill_symbol_task`` inside the RQ worker
process (via ``asyncio.run``) — it is no longer called directly from the
FastAPI request thread.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from backend.config.settings import settings as _settings
from backend.database import SessionLocal
from backend.models import Bar
from backend.repositories.bar_repository import (
    bulk_delete_bars,
    prune_bars_older_than,
    upsert_bars,
)
from backend.market_data.services.manager_class import MarketDataManager, market_data_manager

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


def _instantiate_provider(name: str):
    """Resolve a provider name (e.g. 'alpaca', 'webull', 'yahoo_finance') to
    a (cached) instance.

    Delegates to ``manager.get_cached_provider`` — see that function's
    docstring for why this must not construct a fresh instance on every
    call (found live 2026-09-09: doing so was the direct cause of bars
    landing 1-2+ minutes late, not just wasteful). Returns None if the
    name is unknown or construction fails.
    """
    from backend.market_data.services.manager import get_cached_provider
    return get_cached_provider(name)


# Phase 3.3.12: max 2 concurrent backfills to stay under provider rate limits
# (per event loop — see _get_backfill_semaphore above).


# ---------------------------------------------------------------------------
# Tier 1 — 1m bars: Alpaca primary + yfinance gap-fill
# ---------------------------------------------------------------------------

def _alpaca_range_for_days(days: int) -> str:
    """Map a day count to the provider range string used for 1m fetches.

    Shared between the tier-1 fetch and its gap-fill pass so the two use
    the same window — the gap-fill pass used to hardcode "15d"
    independent of ``BACKFILL_1M_DAYS``, so a gap older than 15 days but
    within the configured (larger) window could never be patched even
    though the initial fetch itself did cover that range (2026-09-08 fix,
    found via a post-redesign completeness audit).
    """
    if days <= 1:
        return "1d"
    elif days <= 5:
        return "5d"
    elif days <= 15:
        return "15d"  # Phase 3.9: 15 trading days
    elif days <= 30:
        return "1mo"
    else:
        return "3mo"  # free-tier cap


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

    Every provider call passes ``include_extended_hours=True`` — confirmed
    live 2026-09-09 that Webull's ``trading_sessions`` param supports
    PRE/RTH/ATH at 1m resolution. Non-Webull providers accept and ignore
    the flag (interface-wide no-op — see BaseMarketDataProvider docstring),
    so this is safe regardless of which provider .env resolves to. Returned
    bars carry their real ``Bar.session`` classification either way;
    sub-hour/higher-timeframe resampling filters to session='regular' (see
    ingestion_service._resample_and_upsert) so this has no effect on any
    existing chart or indicator.
    """
    tf = "1m"
    merged: dict[datetime, Bar] = {}

    # Pick Alpaca range based on requested days.
    alpaca_range = _alpaca_range_for_days(days)

    # 1. Primary from .env (e.g. BACKFILL_1M_PRIMARY=alpaca).
    #
    # ``primary_1m_returned`` (not just "did it return anything") gates
    # the fallback below. Found live 2026-09-11: Webull's free tier
    # silently downgrades M1 to M5 for thin symbols — the provider
    # correctly detects and re-stamps those bars as "5m" (see
    # WebullProvider._parse_bars), so they're never lost, but a batch
    # that's ENTIRELY downgraded still left the old `primary_returned =
    # len(bars)` count positive, which skipped the fallback below even
    # though zero bars actually satisfied the 1m request. A thin
    # ticker's real 1m coverage (e.g. Alpaca's, sparse but genuine)
    # never got a chance to contribute. Symbols Webull serves honestly
    # at 1m (the common case) are unaffected — primary_1m_returned ==
    # len(bars) there, same as before.
    primary_1m_returned = 0
    try:
        from backend.market_data.services.manager import get_backfill_primary_provider
        provider = get_backfill_primary_provider("1m")
        if provider is not None:
            bars = provider.get_historical_bars(
                symbol=symbol, timeframe=tf, range_=alpaca_range,
                include_extended_hours=True,
            )
            for b in bars:
                merged[b.timestamp] = b
            primary_1m_returned = sum(1 for b in bars if b.timeframe == "1m")
            logger.info(f"tier1 1m: {provider.__class__.__name__} returned {len(bars)} bars for {symbol} (range_={alpaca_range}, actually_1m={primary_1m_returned})")
            if bars and primary_1m_returned == 0:
                logger.info(
                    f"tier1 1m: {provider.__class__.__name__} downgraded {symbol} to a "
                    f"coarser resolution ({bars[0].timeframe}) — trying fallback for genuine 1m coverage"
                )
    except Exception as e:
        logger.warning(f"tier1 1m: primary provider failed for {symbol}: {e}")

    # 2. Fallback chain — runs if primary returned zero bars, OR its
    # response didn't actually contain any 1m-resolution bars (see the
    # primary_1m_returned comment above).
    # Providers are loaded from BACKFILL_1M_FALLBACK in .env
    # (default: webull, yahoo_finance). First to return wins.
    # Phase 3.9: use the same window as the primary so Webull's paginator
    # can produce up to ~5,850 bars across 4 pages.
    if primary_1m_returned == 0:
        from backend.market_data.services.manager import get_1m_fallback_providers
        for fb_name in get_1m_fallback_providers():
            try:
                prov = _instantiate_provider(fb_name)
                if prov is None:
                    continue
                bars = prov.get_historical_bars(
                    symbol=symbol, timeframe=tf, range_=alpaca_range,
                    include_extended_hours=True,
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
                symbol=symbol, timeframe=tf, range_="2d",
                include_extended_hours=True,
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

    Every bar is run through ``_normalize_1d_bar`` — webull/alpaca stamp
    daily bars at 00:00, yahoo_finance at 09:30. Without normalizing, those
    conventions never collide on the DB's unique key, so any trading day a
    fallback touched got a SECOND, independent 1d row instead of updating
    the existing one. Found live: 1,501 trading days (63% of all stored 1d
    rows) duplicated this way (2026-09-09 fix). Also fixed: the primary
    branch used to ``return bars`` unconditionally on ANY non-exception
    result — including an empty list — which skipped the fallback chain
    entirely whenever the primary "succeeded" with nothing.
    """
    from backend.market_data.services.ingestion_service import _normalize_1d_bar
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
            raw: list[Bar] = provider.get_historical_bars(
                symbol=symbol, timeframe="1d", range_="5y"
            )
            primary_name = provider.__class__.__name__
            bars = [
                n for n in (_normalize_1d_bar(b, primary_name) for b in raw)
                if n is not None
            ]
            # Filter to the requested window.
            bars = [b for b in bars if _after_cutoff(b)]
            # Drop 13:30 ET noise from Alpaca free tier (normalization
            # already filters this out — kept as a harmless second check).
            bars = [
                b for b in bars
                if not (b.timestamp.hour == 13 and b.timestamp.minute == 30)
            ]
            bars.sort(key=_utc_key)
            logger.debug(f"tier3 1d: {primary_name} returned {len(bars)} bars for {symbol}")
            if bars:
                return bars
    except Exception as e:
        logger.warning(f"tier3 1d: primary provider failed for {symbol}: {e}")

    # Try fallback providers (BACKFILL_1D_FALLBACK in .env) — reached both
    # when the primary raised AND when it "succeeded" with zero bars.
    for fb_name in get_1h_1d_fallback_providers("1d"):
        try:
            provider = _instantiate_provider(fb_name)
            if provider is None:
                continue

            raw = provider.get_historical_bars(
                symbol=symbol, timeframe="1d", range_="2y"
            )
            bars = [
                n for n in (_normalize_1d_bar(b, fb_name) for b in raw)
                if n is not None
            ]
            bars = [b for b in bars if _after_cutoff(b)]
            bars.sort(key=_utc_key)
            logger.debug(f"tier3 1d: {fb_name} returned {len(bars)} bars for {symbol}")
            if bars:
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


# Adjacency threshold per timeframe for _count_contiguous_spans — two
# consecutive missing timestamps (already sorted) count as the SAME outage
# if they're within this of each other, else as separate outages. Sized to
# bridge a normal session/weekend boundary (so one multi-day outage isn't
# reported as dozens of "gaps") without merging genuinely unrelated,
# far-apart missing bars into one. Purely a reporting concern — the actual
# gap-fill matching below always operates on individual timestamps.
_GAP_SPAN_THRESHOLDS: dict[str, timedelta] = {
    "1m": timedelta(minutes=2), "2m": timedelta(minutes=4), "3m": timedelta(minutes=6),
    "5m": timedelta(minutes=10), "15m": timedelta(minutes=30), "30m": timedelta(minutes=60),
    "1h": timedelta(hours=2), "4h": timedelta(hours=8), "1d": timedelta(days=4),
}


def _count_contiguous_spans(gaps: list[datetime], timeframe: str) -> int:
    """Count how many separate outages ``gaps`` (sorted, from ``find_gaps``)
    represents, instead of just the raw bar count.

    find_gaps intentionally returns a flat list of individual missing
    timestamps (see its docstring) — the right shape for "is this specific
    timestamp missing" lookups, which is all the actual patch loop below
    needs. But a flat count is misleading for a human/log/status reader: a
    single 3-day outage (72 missing 1h bars) and 72 scattered single-bar
    holes are very different situations, and reporting "gaps_found: 72" for
    both erases that distinction (found via a 2026-09-08 post-redesign
    completeness audit — the plan had actually specified find_gaps return
    coalesced ranges for exactly this reason; kept the simpler flat-list
    return since the patch loop benefits more from it, but fixed the
    misleading count/log downstream instead).
    """
    if not gaps:
        return 0
    threshold = _GAP_SPAN_THRESHOLDS.get(timeframe, timedelta(days=1))
    spans = 1
    for prev, cur in zip(gaps, gaps[1:]):
        if cur - prev > threshold:
            spans += 1
    return spans


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def _check_and_fill_gaps(
    db,
    symbol: str,
    timeframe: str,
    fallback_provider_names: list[str],
    range_: str,
) -> dict:
    """After a tier's fetch-and-write, verify the DB has no missing expected
    bars across the range just written, and make one more pass through
    EVERY configured fallback provider (not just the first one to respond,
    which is all the tier fetch itself tries) to patch what's missing.

    Providers only expose range-based fetches, not arbitrary start/end
    windows (checked against every ``get_historical_bars`` signature in
    this codebase — none take start/end) — so "targeted" here means:
    re-fetch each fallback provider's normal range, then keep only the
    bars whose timestamp lands on a detected gap. Cheap relative to the
    tier fetch itself (bounded to symbols that actually have gaps, at
    most once per tier per backfill).

    Never raises — a provider failure during the patch pass is logged and
    skipped, same as the rest of this module. Returns
    ``{"gaps_found": N, "gaps_filled": M, "remaining_gap_count": N-M}``.
    """
    from backend.models.market_data_sql import BarModel
    from backend.repositories.bar_repository import find_gaps
    from backend.utils.timezone import to_ny

    row = (
        db.query(
            func.min(BarModel.timestamp), func.max(BarModel.timestamp)
        )
        .filter(BarModel.symbol == symbol.upper(), BarModel.timeframe == timeframe)
        .one()
    )
    start, end = row
    if start is None or end is None:
        return {"gaps_found": 0, "gaps_filled": 0, "remaining_gap_count": 0, "gap_span_count": 0}

    try:
        gaps = find_gaps(db, symbol, timeframe, start, end)
    except ValueError:
        # Timeframe not covered by find_gaps (shouldn't happen for
        # 1m/1h/1d, the only timeframes this is called with).
        return {"gaps_found": 0, "gaps_filled": 0, "remaining_gap_count": 0, "gap_span_count": 0}
    if not gaps:
        return {"gaps_found": 0, "gaps_filled": 0, "remaining_gap_count": 0, "gap_span_count": 0}

    gap_set = set(gaps)
    span_count = _count_contiguous_spans(gaps, timeframe)
    logger.info(
        f"backfill {symbol}: {len(gaps)} missing {timeframe} bars detected "
        f"across {span_count} separate gap(s) spanning {gaps[0]}..{gaps[-1]} "
        f"— patching from fallback chain"
    )

    normalize_fn = None
    if timeframe == "1h":
        from backend.market_data.services.ingestion_service import _normalize_1h_bar as normalize_fn
    elif timeframe == "1d":
        from backend.market_data.services.ingestion_service import _normalize_1d_bar as normalize_fn

    patched: list[Bar] = []
    for fb_name in fallback_provider_names:
        if not gap_set:
            break
        try:
            provider = _instantiate_provider(fb_name)
            if provider is None:
                continue
            raw = provider.get_historical_bars(symbol=symbol, timeframe=timeframe, range_=range_)
            for b in raw:
                if normalize_fn:
                    b = normalize_fn(b, fb_name)
                    if b is None:
                        continue
                ts = to_ny(b.timestamp)
                if ts in gap_set:
                    b.timestamp = ts
                    b.timeframe = timeframe
                    patched.append(b)
                    gap_set.discard(ts)
        except Exception as e:
            logger.debug(f"backfill {symbol}: gap-fill pass via {fb_name} failed: {e}")
            continue

    filled = 0
    if patched:
        filled = await _write_bars_in_chunks(db, patched)

    remaining = len(gap_set)
    if remaining:
        logger.info(
            f"backfill {symbol}: {remaining} {timeframe} bars remain missing "
            f"after the gap-fill pass — no configured provider has them "
            f"(recorded, not retried further this run)"
        )
    elif filled:
        logger.info(f"backfill {symbol}: gap-fill patched all {len(gaps)} missing {timeframe} bars")

    return {
        "gaps_found": len(gaps), "gaps_filled": filled, "remaining_gap_count": remaining,
        "gap_span_count": span_count,
    }


async def backfill_symbol_history(symbol: str, days: int | None = None) -> dict:
    """Backfill bar history for ``symbol`` covering ``days`` calendar days.

    Downloads three tiers, each window configurable via .env (falls back to
    the hardcoded default shown, always capped by ``min(tier_days, days)``):
      - Tier 1: 1m bars — BACKFILL_1M_DAYS (default 15d) — primary + gap-fill
      - Tier 2: 1h bars — BACKFILL_1H_DAYS (default 365d) — primary + fallback
      - Tier 3: 1d bars — BACKFILL_1D_DAYS (default 1095d) — primary + fallback

    Each tier is followed by a gap-check-and-fill pass (``_check_and_fill_gaps``)
    BEFORE the timeframes that derive from it are resampled — 2m/3m/5m/15m/30m
    depend on tier 1 being as complete as the provider chain can make it, 4h
    on tier 2, 1wk on tier 3.

    Single-flight and the "at most N concurrent" cap are enforced upstream
    now, by ``backfill_queue.enqueue_backfill`` (DB + RQ check) before a job
    ever reaches this function, and by running N worker processes — this
    function itself has no lock/semaphore of its own (see the module
    docstring for why that used to be here and isn't anymore).

    Returns:
        ``{"symbol", "tier1_written", "tier2_written", "tier3_written",
           "gaps_found", "gaps_filled", "gap_detail", "duration_s"}``
    """
    settings = _settings
    # Optional caller override: when given, clamps every tier DOWN (never
    # up past its own BACKFILL_*_DAYS ceiling) — e.g.
    # scripts/backfill_1000d.py --days=90 for a smaller/faster reseed.
    # None (every real production call site) means each tier just uses
    # its own default, unclamped.
    #
    # Removed 2026-09-09 (MARKET_DATA_BAR_RETENTION_DAYS): this used to
    # default from that setting via `days if days is not None else
    # settings.market_data.bar_retention_days`, but that setting
    # (1095) was, by construction, always >= every tier's own days
    # value (15 / 365 / 1095) — so `min(tier_default, retention_days)`
    # never actually clamped anything in any real call path. Storage
    # retention is now handled properly and separately by
    # settings.retention (see RetentionSettings) — this was purely a
    # dead fetch-depth cap that happened to never bind.
    override_days = max(1, days) if days is not None else None

    def _tier_days(tier_default: int) -> int:
        return min(tier_default, override_days) if override_days is not None else tier_default

    symbol = symbol.upper()
    start_time = time.monotonic()

    logger.info(
        f"backfill_symbol_history: starting backfill for {symbol} "
        f"({f'override={override_days}d' if override_days is not None else 'tier defaults'})"
    )

    db = SessionLocal()
    try:
        # Reuse the process-wide manager. This used to build a fresh
        # MarketDataManager() per job, which constructs and authenticates every
        # provider (Webull's signed handshake included) — for an argument that
        # _fetch_tier1_1m_bars never reads. Every ticker added to a watchlist and
        # every RQ backfill job paid that, on the event loop.
        manager = market_data_manager
        tier1_written = 0
        tier2_written = 0
        tier3_written = 0
        gap_detail: dict[str, dict] = {}

        # Tier 1: 1m bars (Alpaca primary + yfinance gap-fill).
        # Phase 3.9: extend to 15 trading days by default (now
        # configurable via BACKFILL_1M_DAYS). WebullProvider paginates
        # internally (4 pages × 1,650 = 6,600 bars max) when range_="15d"
        # is passed to get_historical_bars, so primary providers can now
        # cover the full 15-day window in one call.
        tier1_days = _tier_days(settings.backfill.tf_1m_days)
        tier1_bars = await _fetch_tier1_1m_bars(
            symbol, tier1_days, manager, db
        )
        if tier1_bars:
            tier1_written = await _write_bars_in_chunks(db, tier1_bars)
            try:
                from backend.market_data.services.manager import get_1m_fallback_providers
                gap_detail["1m"] = await _check_and_fill_gaps(
                    db, symbol, "1m", get_1m_fallback_providers(),
                    _alpaca_range_for_days(tier1_days),
                )
            except Exception as e:
                logger.warning(f"backfill {symbol}: 1m gap-check failed: {e}")

        # Auto-resample sub-hour timeframes from the now gap-checked 1m
        # (lazy import to avoid circular dependency). Always resample —
        # upsert is idempotent so re-running when tier1_written=0
        # (re-backfill of existing bars) is safe and ensures
        # 2m/3m/5m/15m/30m bars are populated.
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
        tier2_days = _tier_days(settings.backfill.tf_1h_days)
        tier2_bars = await _fetch_tier2_1h_bars(symbol, tier2_days)
        if tier2_bars:
            tier2_written = await _write_bars_in_chunks(db, tier2_bars)
            try:
                from backend.market_data.services.manager import get_1h_1d_fallback_providers
                gap_detail["1h"] = await _check_and_fill_gaps(
                    db, symbol, "1h", get_1h_1d_fallback_providers("1h"), "5y"
                )
            except Exception as e:
                logger.warning(f"backfill {symbol}: 1h gap-check failed: {e}")

        # Correct 1h using our own 1m data wherever 1m coverage exists
        # (RETENTION_TF_1M_DAYS). Found live 2026-09-09: Webull's 1h
        # endpoint (the primary source above) returns bars anchored at
        # :30, not :00 — _normalize_1h_bar floors those to the preceding
        # :00, silently mislabeling which hour a bar's high/low actually
        # belong to (a bar spanning [10:30,11:30) got filed under "10:00"
        # even though its extremes could easily have occurred after
        # 11:00). No floor/ceiling choice fixes that — the bar genuinely
        # straddles two canonical hours. Our own 1m data has none of that
        # ambiguity, so this overwrites tier2's bars with the verified
        # aggregate wherever 1m is available. See
        # ingestion_service._resample_1h_from_1m_and_upsert's docstring.
        try:
            from backend.market_data.services.ingestion_service import ingestion_service
            from backend.config.settings import settings as _settings_1h
            from backend.utils.timezone import NY as _NY_TZ_local
            now_ny = datetime.now(_NY_TZ_local).replace(tzinfo=None)
            window_start = now_ny - timedelta(days=_settings_1h.retention.tf_1m_days)
            hours = ingestion_service._hour_starts_between(window_start, now_ny)
            corrected = await ingestion_service._resample_1h_from_1m_and_upsert(
                hour_starts=hours, _symbol=symbol,
            )
            if corrected:
                logger.info(f"backfill {symbol}: corrected {corrected} 1h bars from 1m")
        except Exception as e:
            logger.warning(f"backfill {symbol}: 1h correction-from-1m failed: {e}")

        # Tier 3: 1d bars. Window configurable via BACKFILL_1D_DAYS
        # (default 1095 ≈ 3 years), still capped by an explicit override.
        tier3_days = _tier_days(settings.backfill.tf_1d_days)
        if tier3_days > 0:
            tier3_bars = await _fetch_tier2_1d_bars(
                symbol,
                days_start=tier3_days,
            )
            if tier3_bars:
                tier3_written = await _write_bars_in_chunks(db, tier3_bars)
                try:
                    from backend.market_data.services.manager import get_1h_1d_fallback_providers
                    gap_detail["1d"] = await _check_and_fill_gaps(
                        db, symbol, "1d", get_1h_1d_fallback_providers("1d"), "5y"
                    )
                except Exception as e:
                    logger.warning(f"backfill {symbol}: 1d gap-check failed: {e}")

        # Auto-resample higher timeframes from whatever 1h/1d data
        # exists — NOT gated on tier2_written/tier3_written > 0.
        # Those only count rows written by THIS call; a symbol whose
        # 1h/1d was already fully populated by an earlier, separate fetch
        # (e.g. ingestion_service.register_symbol's own live-loop pickup,
        # which can land before this job runs) legitimately writes 0 new
        # rows here while still having plenty of data to resample from.
        # Gating on this call's delta skipped 4h/1wk entirely whenever
        # that race landed the "wrong" way (2026-09-09 fix, found via
        # SOFI). Both resample functions are cheap, idempotent upserts
        # that already no-op internally when there isn't enough source
        # data, so always attempting them is safe.
        try:
            written_4h = await ingestion_service._resample_1h_to_4h_and_upsert(_symbol=symbol)
            if written_4h:
                logger.info(f"backfill {symbol}: auto-resampled {written_4h} 4h bars from 1h")
        except Exception as e:
            logger.warning(f"backfill {symbol}: 4h resample failed: {e}")

        try:
            written_1wk = await ingestion_service._resample_1d_to_1wk_and_upsert(_symbol=symbol)
            if written_1wk:
                logger.info(f"backfill {symbol}: auto-resampled {written_1wk} 1wk bars from 1d")
        except Exception as e:
            logger.warning(f"backfill {symbol}: 1wk resample failed: {e}")

        gaps_found = sum(d["gaps_found"] for d in gap_detail.values())
        gaps_filled = sum(d["gaps_filled"] for d in gap_detail.values())

        duration = time.monotonic() - start_time
        logger.info(
            f"backfill_symbol_history: {symbol} done in {duration:.1f}s — "
            f"tier1(1m)={tier1_written}, tier2(1h)={tier2_written}, "
            f"tier3(1d)={tier3_written}, gaps={gaps_filled}/{gaps_found} filled"
        )
        return {
            "symbol": symbol,
            "tier1_written": tier1_written,
            "tier2_written": tier2_written,
            "tier3_written": tier3_written,
            "gaps_found": gaps_found,
            "gaps_filled": gaps_filled,
            "gap_detail": gap_detail,
            "duration_s": round(duration, 2),
        }
    finally:
        db.close()


def backfill_symbol_task(symbol: str, job_id: str) -> None:
    """RQ job body — the ONLY call site for ``backfill_symbol_history`` now.

    Runs in the RQ worker process. Sync (RQ jobs are sync callables) —
    drives the async pipeline via ``asyncio.run``, which is safe here
    specifically because the worker process runs no ingestion_service loop
    of its own to race against (unlike the old dual-trigger design this
    replaced — see the module docstring).

    Writes status/progress to the ``BackfillJob`` row identified by
    ``job_id`` at start, and at completion (success, partial, or failure)
    — see ``backend/market_data/services/backfill_queue.py`` for how that
    row is created and polled.
    """
    asyncio.run(_run_backfill_job(symbol, job_id))


async def _run_backfill_job(symbol: str, job_id: str) -> None:
    from backend.models import BackfillJob

    db = SessionLocal()
    try:
        job = db.query(BackfillJob).filter(BackfillJob.job_id == job_id).first()
        if job is not None:
            job.status = "started"
            job.started_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()

    try:
        result = await backfill_symbol_history(symbol)
    except Exception as e:
        logger.error(f"backfill job {job_id} ({symbol}) failed: {e}")
        db = SessionLocal()
        try:
            job = db.query(BackfillJob).filter(BackfillJob.job_id == job_id).first()
            if job is not None:
                job.status = "failed"
                job.error = str(e)[:2000]
                job.completed_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()
        return

    try:
        db = SessionLocal()
        try:
            job = db.query(BackfillJob).filter(BackfillJob.job_id == job_id).first()
            if job is not None:
                remaining = result["gaps_found"] - result["gaps_filled"]
                job.tier1_written = result["tier1_written"]
                job.tier2_written = result["tier2_written"]
                job.tier3_written = result["tier3_written"]
                job.gaps_found = result["gaps_found"]
                job.gaps_filled = result["gaps_filled"]
                job.result = json.dumps(result.get("gap_detail", {}))
                job.status = "completed" if remaining == 0 else "partial"
                job.completed_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()
    except Exception as e:
        # backfill_symbol_history already succeeded and wrote real bars —
        # only this status-row write failed (e.g. a concurrent-writer
        # SQLite lock timeout with two RQ workers sharing one DB file).
        # Without this except, that left the BackfillJob row stuck at
        # status="started" forever: RQ itself doesn't touch our row (a
        # raise here would just propagate to Worker.perform_job's own
        # bare except, which marks the RQ job failed but never reconciles
        # our DB row), and get_backfill_job_status only ever promotes
        # queued/started -> started from RQ's live status, never notices
        # a started row whose job has actually finished. Found via a
        # 2026-09-08 post-redesign completeness audit. Mark it failed
        # here instead so a poller doesn't see "started" indefinitely for
        # a backfill that actually completed.
        logger.error(f"backfill job {job_id} ({symbol}): status-row write failed after a successful backfill: {e}")
        try:
            db = SessionLocal()
            try:
                job = db.query(BackfillJob).filter(BackfillJob.job_id == job_id).first()
                if job is not None:
                    job.status = "failed"
                    job.error = f"backfill succeeded but the status write failed: {e}"[:2000]
                    job.completed_at = datetime.utcnow()
                    db.commit()
            finally:
                db.close()
        except Exception as e2:
            logger.error(f"backfill job {job_id} ({symbol}): failed to even mark the status-write failure: {e2}")
        return

    # Immediately record signals for the newly backfilled bars so the
    # dashboard shows data without waiting for the next signal-recording
    # loop tick — mirrors what the old (now-removed) _do_backfill did.
    try:
        from backend.services.signal_recorder import signal_recorder
        recorded = signal_recorder.backfill_signals_for_symbol(symbol.upper())
        if recorded:
            logger.info(f"backfill job {job_id}: recorded {recorded} signals for {symbol}")
    except Exception as e:
        logger.warning(f"backfill job {job_id}: signal recording failed: {e}")


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
