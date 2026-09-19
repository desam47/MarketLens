"""
Market Data Ingestion Service
Automatically fetches and stores market data from providers
"""
import asyncio
import atexit
import logging
import random
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import (
    Bar,
    BarModel,
    DataStatus,
    MarketStatus,
    MarketStatusModel,
    ProviderStatusModel,
    Quote,
)
from backend.observability import record_bar, record_bars, set_ingestion_running
from backend.repositories import quote_repository
from backend.repositories.watchlist_repository import WatchlistRepository
from backend.services.signal_recorder import signal_recorder

from .engine_seeder import engine_registry
from .engine_seeder import _ensure_aware
from .manager import MarketDataManager

logger = logging.getLogger(__name__)

# Shared NY timezone instance — created once at module load.
_NY_TZ = ZoneInfo("America/New_York")


def _instantiate_backfill_provider(name: str):
    """Resolve a backfill provider name (e.g. 'alpaca', 'webull', 'yahoo_finance')
    to a (cached) instance, for the 1h ingestion loops' fallback path.

    Delegates to ``manager.get_cached_provider`` — see that function's
    docstring for why this must not construct a fresh instance on every
    call (found live 2026-09-09: doing so was the direct cause of bars
    landing 1-2+ minutes late, not just wasteful). This helper used to
    construct fresh every call itself — a second, independent copy of
    that same bug that survived the 2026-09-09 fix to its sibling in
    backfill_service.py because nothing pointed the two at a shared
    implementation. Returns None if the name is unknown or construction
    fails.
    """
    from backend.market_data.services.manager import get_cached_provider
    return get_cached_provider(name)


def _normalize_1h_bar(b: Bar, provider_name: str) -> Bar | None:
    """Normalize a 1h bar timestamp to a clean :00 boundary.

    All providers in our chain (Alpaca, Webull, yfinance) should align to
    either the top of the hour (:00) or market-hour anchors (:30) that
    represent the same hourly candle. yfinance uses :30; Alpaca and Webull
    use :00. This function floors :30 bars to the preceding :00 so the
    DB's (symbol, timeframe, timestamp) unique-key matches across providers.

    Note: yfinance is the only provider that includes the 16:00 ET close bar.
    The fallback chain tries yfinance after Alpaca precisely to capture that
    bar. Do NOT switch the canonical offset to :30 here — Alpaca and Webull
    bars would then collide (and the 16:00 from yfinance would still need
    an extra shift).

    Bars with other offsets are logged and skipped.

    Returns a new Bar with the normalized timestamp, or None if the bar
    should be skipped.
    """
    ts = b.timestamp
    minute = ts.minute
    second = ts.second

    if minute == 0 and second == 0:
        # Clean :00 bar — use as-is (Alpaca, Webull)
        return b

    if minute == 30 and second == 0:
        # yfinance-style 30-min offset — floor to the top of the hour
        normalized_ts = ts.replace(minute=0, second=0, microsecond=0)
        return Bar(
            symbol=b.symbol,
            timestamp=normalized_ts,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
            timeframe=b.timeframe,
            provider=b.provider,
            data_status=b.data_status,
        )

    # Unexpected offset — skip and log once per symbol per loop run
    # (logger.debug to avoid noise; raise to surface if needed during testing)
    logger.debug(
        f"Skipping 1h bar for {b.symbol} with unexpected offset "
        f"(minute={minute}, second={second}) from {provider_name}"
    )
    return None


def _normalize_1d_bar(b: Bar, provider_name: str) -> Bar | None:
    """Normalize a 1d bar timestamp to a clean midnight (00:00) boundary.

    Webull and Alpaca stamp daily bars at 00:00 local; yahoo_finance stamps
    them at 09:30 (RTH open) instead. Without this, the two conventions
    never collide on the DB's (symbol, timeframe, timestamp) unique key —
    every trading day a fallback to yahoo_finance touched gets a SECOND,
    independent 1d row alongside the 00:00 one, with its own (different!)
    OHLCV. Found live: 1,501 trading days (63% of all stored 1d rows)
    duplicated this way, with close prices differing by up to 2.4% between
    the two rows for the same day — which one a query returns is whatever
    happens to sort first, not a deliberate choice (2026-09-09 fix).

    Mirrors _normalize_1h_bar's approach: floor the known alternate offset
    to the canonical one; skip (log) anything unrecognized rather than
    risk silently mis-bucketing it.
    """
    ts = b.timestamp
    hour, minute, second = ts.hour, ts.minute, ts.second

    if hour == 0 and minute == 0 and second == 0:
        return b  # Clean midnight bar — use as-is (webull, alpaca)

    if hour == 9 and minute == 30 and second == 0:
        # yfinance-style RTH-open offset — floor to midnight.
        normalized_ts = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        return Bar(
            symbol=b.symbol,
            timestamp=normalized_ts,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
            timeframe=b.timeframe,
            provider=b.provider,
            data_status=b.data_status,
        )

    logger.debug(
        f"Skipping 1d bar for {b.symbol} with unexpected offset "
        f"(hour={hour}, minute={minute}, second={second}) from {provider_name}"
    )
    return None


# The 1m recent-window loop only needs the newest few bars: it runs every ~60s,
# and _gapfill_1m_loop / the backfill jobs own gap healing. Not every provider
# honours ``range_="15m"``: Webull treated it as a whole trading day (891 bars
# per symbol) and Yahoo's range table maps "15m" to 5 days (~1,900 bars per
# symbol), which had this loop upserting 22,000-46,000 rows a minute — ~2-4 s of
# GIL-bound work that starved the API's event loop, and past SQLite's
# bound-variable limit the whole cycle was lost. 30 = the "15m" lookback doubled
# for safety (webull_provider._RANGE_TO_COUNT).
_RECENT_WINDOW_MAX_BARS = 30


def _newest_bars(bars: list, limit: int = _RECENT_WINDOW_MAX_BARS) -> list:
    """Return at most the ``limit`` newest bars, in chronological order."""
    if len(bars) <= limit:
        return bars
    return sorted(bars, key=lambda b: b.timestamp)[-limit:]


class MarketDataIngestionService:
    """Service for automatically ingesting and storing market data"""

    def __init__(self, symbols: list[str] = None, timeframes: list[str] = None):
        """
        Initialize the ingestion service

        Args:
            symbols: List of symbols to track (default: loaded from active watchlist on start)
            timeframes: List of timeframes to track (default: common timeframes)
        """
        # Defer watchlist loading until start() — DB may not be ready at __init__ time.
        self.symbols = symbols or []
        # Phase 3.1: store only 1m bars. Higher timeframes are derived at
        # read time by resample_ohlcv() in bar_repository.get_bars().
        self.timeframes = timeframes or ["1m"]
        self.manager = MarketDataManager()
        self.is_running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self.last_quote_update: dict[str, datetime] = {}
        self.last_bar_update: dict[str, dict[str, datetime]] = {}
        self.last_status_update: dict[str, datetime] = {}

        # Initialize tracking dictionaries
        for symbol in self.symbols:
            self.last_quote_update[symbol] = datetime.min
            self.last_status_update[symbol] = datetime.min
            self.last_bar_update[symbol] = {tf: datetime.min for tf in self.timeframes}
            # Phase 3.1: prune any pre-existing non-1m timeframe entries from
            # the dict. Since we only ingest 1m bars, higher-TF keys are never
            # written and should not linger from a pre-3.1 state.
            self.last_bar_update[symbol] = {
                k: v for k, v in self.last_bar_update[symbol].items()
                if k == "1m"
            }

    def _load_symbols_from_watchlist(self) -> list[str]:
        return self._load_symbols_and_watchlists_from_all_active_watchlists()[0]

    def _load_symbols_and_watchlists_from_all_active_watchlists(
        self, include_symbols: bool = True
    ) -> tuple[list[str], list[str]]:
        """Load symbols and contributing watchlist names from active watchlists.

        Symbols are de-duplicated and sorted for deterministic ingestion. Watchlist
        names retain newest-first database order and only include lists with at
        least one enabled symbol.
        """
        try:
            db = SessionLocal()
            try:
                repo = WatchlistRepository(db)
                active_wls = repo.get_watchlists(active_only=True)
                seen = set()
                symbols: list[str] = []
                watchlists: list[str] = []
                for wl in active_wls:
                    watchlist_symbols = repo.get_watchlist_symbols(wl.id, enabled_only=True)
                    if watchlist_symbols:
                        watchlists.append(wl.name)
                    if include_symbols:
                        for ws in watchlist_symbols:
                            sym = ws.symbol.upper()
                            if sym not in seen:
                                seen.add(sym)
                                symbols.append(sym)
                if include_symbols:
                    if symbols:
                        logger.info(f"Loaded {len(symbols)} symbols across {len(active_wls)} active watchlist(s): {symbols}")
                    else:
                        if active_wls:
                            logger.info("Active watchlists exist but all are empty — no symbols to ingest")
                        else:
                            logger.info("No active watchlist found — no symbols to ingest")
                return (sorted(symbols), watchlists) if include_symbols else ([], watchlists)
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Failed to load symbols from all active watchlists: {e}")
            return [], []

    def get_tracking_watchlists(self) -> list[str]:
        """Return active watchlists that contribute enabled symbols."""
        return self._load_symbols_and_watchlists_from_all_active_watchlists(include_symbols=False)[1]

    def start(self):
        """Start the ingestion service in a background thread.

        This is a *sync* method so it works correctly with FastAPI's
        BackgroundTasks (which only handles sync callables). The background
        thread owns its own asyncio event loop, so the async ingestion loops
        run in isolation without blocking the request thread.

        The correlation ID from the calling request's context is captured at
        start time and restored in the daemon thread, so ingestion loop log
        lines are traceable back to the initiating request.
        """
        if self.is_running:
            logger.warning("Ingestion service is already running")
            return

        # Load symbols from active watchlist if none were explicitly set.
        # Done here (not in __init__) so the DB is guaranteed to be ready.
        if not self.symbols:
            self.symbols = self._load_symbols_and_watchlists_from_all_active_watchlists()[0]
            # Only ingest what the watchlist contains. If no watchlist has symbols,
            # ingestion is a no-op — everything shows empty until the user adds stocks.
            if self.symbols:
                logger.info(f"Loaded {len(self.symbols)} symbols from watchlist: {self.symbols}")
            # Re-initialise tracking dicts for the loaded symbols.
            for symbol in self.symbols:
                if symbol not in self.last_quote_update:
                    self.last_quote_update[symbol] = datetime.min
                    self.last_status_update[symbol] = datetime.min
                    # Phase 3.1: only track 1m updates. Higher TFs are
                    # resampled at read time, so we never want to throttle
                    # ingestion based on them.
                    self.last_bar_update[symbol] = {"1m": datetime.min}

        self.is_running = True
        set_ingestion_running(True)
        logger.info(f"Starting market data ingestion service for {len(self.symbols)} symbols in background thread")

        # Capture the correlation ID from the current contextvar so it can be
        # re-installed in the background thread. Daemon threads don't inherit
        # contextvars from the parent thread, and asyncio.run() creates a fresh
        # task that doesn't inherit them either.
        captured_corr_id: str | None = None
        try:
            from backend.observability.logging_enhanced import get_correlation_id, set_correlation_id
            captured_corr_id = get_correlation_id()
        except Exception:
            pass

        def _run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                # Re-install the captured correlation ID into the asyncio context.
                if captured_corr_id:
                    try:
                        set_correlation_id(captured_corr_id)
                    except Exception:
                        pass
                # Phase 3.3.16: seed-check — backfill any symbol whose oldest
                # bar is more than 700 days old (stale DB state). Run this
                # before the loops so the ingestion pipeline starts with
                # fresh data.
                self._loop.run_until_complete(self._seed_check())
                # Populate 4h and 1wk bars immediately so the chart has
                # data before the first scheduled loop fire.
                self._loop.run_until_complete(self._startup_resample_tiers())
                self._loop.run_until_complete(self._run_loops())
            except asyncio.CancelledError:
                # stop() was called — cancelled is a clean exit, not an error.
                pass
            finally:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
                self._loop.close()
                self._loop = None
                self.is_running = False
                set_ingestion_running(False)
                logger.info("Ingestion service loop exited")

        self._thread = threading.Thread(target=_run_loop, daemon=True, name="ingestion")
        self._thread.start()

    # ------------------------------------------------------------------
    # Phase 3.7 — multi-timeframe live ingestion + resample-at-write
    # ------------------------------------------------------------------

    # Sub-hour timeframes resampled from 1m bars every 2 min.
    _SUBHOUR_TFS: list[str] = ["2m", "3m", "5m", "15m", "30m"]

    # Widening per target TF (hours), mirroring bar_repository._WIDENING_HOURS.
    #
    # 15m gets the same padding as 30m (not 0, like 2m/3m/5m) — with 0
    # widening it shared 5m's 60-min base window but had to fill 3x
    # larger buckets from it, leaving only ~4 complete buckets of
    # redundancy vs 5m's ~12. A brief 1m-feed hiccup (provider hiccups
    # are common — see this file's gap-fill loop) could leave too few 1m
    # bars to close 15m's newest bucket at all, stalling it for several
    # 2-min passes while 5m/30m kept updating normally. Confirmed live
    # 2026-09-16: DVLT's 15m bar stuck at 11:30 while its 5m bar was
    # already at 12:20 and 30m correctly at 11:30 (30m's own window
    # genuinely hadn't closed yet — not the same issue).
    _RESAMPLE_WIDENING_HOURS: dict[str, int] = {
        "2m": 0, "3m": 0, "5m": 0, "15m": 1, "30m": 1,
        "1h": 1, "4h": 4, "1d": 24, "1wk": 168,
    }

    # Per-source-TF minute counts, mirroring resampler._TF_MINUTES.
    _RESAMPLE_SOURCE_MINS: dict[str, int] = {
        "1m": 1, "5m": 5, "15m": 15, "30m": 30,
        "1h": 60, "4h": 240, "1d": 1440, "1wk": 10080,
    }

    def _model_to_bar(self, row: BarModel) -> Bar:
        """Convert a BarModel row to a Pydantic Bar (used by resample helpers)."""
        try:
            status = DataStatus(row.data_status)
        except ValueError:
            status = DataStatus.LIVE
        return Bar(
            symbol=row.symbol,
            timeframe=row.timeframe,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            timestamp=row.timestamp,
            provider=row.provider,
            data_status=status,
            source=getattr(row, "source", None),
            session=getattr(row, "session", None) or "regular",
        )

    # ------------------------------------------------------------------
    # 1m → sub-hour resampling (2m/3m/5m/15m/30m)
    # The resampler requires 1m input bars only.
    # ------------------------------------------------------------------

    async def _resample_and_upsert(
        self,
        target_tf: str,
        source_tf: str = "1m",
        _symbol: str | None = None,
        full_history: bool = False,
    ) -> int:
        """Read 1m bars → resample → upsert, including the in-progress bucket.

        Phase 3.7: writes resampled bars to the DB so reads return direct
        rows instead of recomputing on every request. Closed buckets
        (end-time < now) are written HISTORICAL; the still-forming bucket
        is written INCOMPLETE and refreshed on every pass, matching 1d/1h.

        By default, fetches only bars within a narrow recent window (see
        ``full_history`` below) to avoid loading ALL historical 1m bars into
        memory on every ~2-min tick of the live resample loop.

        Args:
            target_tf: target timeframe (2m/3m/5m/15m/30m/1h/4h/1d/1wk)
            source_tf: source timeframe (default "1m")
            _symbol: if provided, resample only this symbol instead of
                self.symbols. Used by backfill_service to ensure newly added
                symbols are resampled even if ingestion hasn't loaded them yet.
            full_history: if True, resample ALL of the symbol's stored 1m
                bars instead of the narrow recent window. The narrow window
                is correct for the live loop (called every ~2 min — no need
                to rescan weeks of history each time) but wrong for a
                one-time post-backfill pass: without this, backfill only
                ever resampled the last ~1-2.5h of freshly-fetched 1m
                history, so 2m/3m/5m/15m/30m stayed sparse (only whatever
                the live loop accumulated since the symbol was added) even
                though years of 1m history existed to resample from
                (2026-09-08 fix). Used by backfill_service for the initial
                sub-hour resample of a newly backfilled symbol.
        """
        from backend.repositories.bar_repository import upsert_bars
        from backend.utils.resampler import resample_ohlcv, ResampleError, _TF_MINUTES

        symbols_to_process = [_symbol] if _symbol else self.symbols
        written = 0
        db = SessionLocal()
        try:
            target_mins = _TF_MINUTES.get(target_tf, 60)
            for symbol in symbols_to_process:
                query = (
                    db.query(BarModel)
                    .filter(
                        and_(
                            BarModel.symbol == symbol.upper(),
                            BarModel.timeframe == source_tf,
                        )
                    )
                )
                # 2026-09-09: sub-hour timeframes (2m/3m/5m/15m/30m — the
                # only targets this function is ever called with, per
                # _SUBHOUR_TFS) now include premarket/after_hours 1m bars,
                # not just regular-session ones — by request, after
                # confirming Webull's extended-hours 1m data works. No
                # session filter needed here: sub-hour bucket boundaries
                # (all divide evenly into the 09:30/16:00/04:00/20:00
                # session edges) never straddle a session, so each
                # resulting bar's session is unambiguous — tagged in
                # upsert_bars, same chokepoint as 1m. 1h/4h/1d/1wk are
                # never resampled through this function (they're fetched
                # directly from providers, or derived from 1h/1d by a
                # different function) and stay regular-session-only.
                if not full_history:
                    # Only fetch bars within the widening window for this
                    # timeframe to avoid loading all historical 1m bars
                    # into memory.
                    #
                    # _RESAMPLE_WIDENING_HOURS is *padding* on top of a base
                    # window sized to cover a few complete target-tf
                    # buckets — it is not the window itself. Using it alone
                    # as the cutoff made the lookback 0h for 2m/3m/5m/15m
                    # (their widening is 0), so the query matched ~no rows
                    # and those timeframes silently stopped resampling.
                    # Guarantee at least a few buckets' worth of 1m history
                    # so there's always something to resample.
                    widening_hours = self._RESAMPLE_WIDENING_HOURS.get(target_tf, 1)
                    base_hours = max(1.0, (target_mins * 3) / 60)
                    cutoff = datetime.now(_NY_TZ).replace(tzinfo=None) - timedelta(
                        hours=base_hours + widening_hours
                    )
                    query = query.filter(BarModel.timestamp >= cutoff)

                rows = query.order_by(BarModel.timestamp.asc()).all()
                if len(rows) < 2:
                    continue
                bars_src = [self._model_to_bar(r) for r in rows]
                try:
                    resampled = resample_ohlcv(bars_src, target_tf)
                except ResampleError:
                    continue

                # DB timestamps are naive NY. Get current NY time as naive for comparison.
                # Write every bucket, not just closed ones — mirrors 1d/1h's
                # live-bucket convention (_resample_1d_live_and_upsert /
                # _resample_1h_from_1m_and_upsert): the still-forming bucket
                # is written INCOMPLETE and re-upserted (same key) on every
                # ~2-min pass until it closes, instead of being invisible
                # until then.
                now = datetime.now(_NY_TZ).replace(tzinfo=None)
                to_write = []
                for bar in resampled:
                    end = bar.timestamp + timedelta(minutes=target_mins)
                    bar.provider = f"aggregated_from_{source_tf}"
                    bar.data_status = DataStatus.HISTORICAL if end < now else DataStatus.INCOMPLETE
                    to_write.append(bar)
                if to_write:
                    written += upsert_bars(db, to_write)
                # Small delay between symbols to avoid bursts
                await asyncio.sleep(0.05)
            db.commit()
            if written:
                from backend.market_data.services.cache import _redis_cache
                for symbol in symbols_to_process:
                    _redis_cache.invalidate_bars_for_symbol(symbol)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return written

    # ------------------------------------------------------------------
    # 1h → 4h aggregation (aligned to NY market-hour boundaries)
    # ------------------------------------------------------------------

    # How far back to look when resampling 1h→4h / 1d→1wk on the live,
    # frequently-run path (full_history=False). Once a bucket closes it
    # never needs to be revisited — these only need to cover however far
    # back a late-arriving bar could plausibly land. _gapfill_1h_once
    # fetches at most the last 5 days of 1h bars, so 10 days gives that
    # comfortable headroom; _write_1d_bars fetches at most 30 days, so 60
    # days does the same for 1d→1wk. Without this cap, both functions
    # re-scanned EVERY stored 1h/1d row for every symbol on every ~2 min
    # tick — unlike the sub-hour resampler, which was already windowed —
    # so the scan cost grew unbounded with total retained history instead
    # of staying flat. full_history=True (startup / explicit backfill
    # calls only) restores the unwindowed full scan.
    _4H_RESAMPLE_WINDOW_DAYS = 10
    _1WK_RESAMPLE_WINDOW_DAYS = 60

    async def _resample_1h_to_4h_and_upsert(
        self, _symbol: str | None = None, full_history: bool = False
    ) -> int:
        """Read 1h bars → aggregate to 4h (NY market hours) → upsert.

        4h buckets: 00:00-03:59, 04:00-07:59, 08:00-11:59, 12:00-15:59,
        16:00-19:59, 20:00-23:59 ET.  Only confirmed-closed buckets
        (end-time < now) are written.

        If ``_symbol`` is provided, resample only that symbol instead of
        ``self.symbols`` (used by backfill_service for newly added symbols).

        By default only the last ``_4H_RESAMPLE_WINDOW_DAYS`` of 1h bars
        are scanned (see the class-level comment above); pass
        ``full_history=True`` for a one-time full backfill (startup).
        """
        from backend.repositories.bar_repository import upsert_bars

        symbols_to_process = [_symbol] if _symbol else self.symbols
        written = 0
        db = SessionLocal()
        try:
            for symbol in symbols_to_process:
                query = db.query(BarModel).filter(
                    and_(
                        BarModel.symbol == symbol.upper(),
                        BarModel.timeframe == "1h",
                    )
                )
                if not full_history:
                    cutoff = datetime.now(_NY_TZ).replace(tzinfo=None) - timedelta(
                        days=self._4H_RESAMPLE_WINDOW_DAYS
                    )
                    query = query.filter(BarModel.timestamp >= cutoff)
                rows = query.order_by(BarModel.timestamp.asc()).all()
                if len(rows) < 4:
                    continue

                bars_src = [self._model_to_bar(r) for r in rows]
                buckets: dict[datetime, list[Bar]] = {}
                for bar in bars_src:
                    # Floor to nearest 4h NY market-hour boundary.
                    # bar.timestamp is naive NY, so localize to NY tz first
                    try:
                        dt_ny = bar.timestamp.replace(tzinfo=_NY_TZ)
                        hour_floor = (dt_ny.hour // 4) * 4
                        bucket_start_ny = dt_ny.replace(hour=hour_floor, minute=0, second=0, microsecond=0, tzinfo=None)
                    except Exception:
                        bucket_start_ny = bar.timestamp
                    if bucket_start_ny not in buckets:
                        buckets[bucket_start_ny] = []
                    buckets[bucket_start_ny].append(bar)

                # DB timestamps are naive NY. Get current NY time as naive for comparison.
                # Live in-progress bucket, same convention as 1d/1h/sub-hour:
                # written INCOMPLETE and refreshed each pass until it closes.
                now = datetime.now(_NY_TZ).replace(tzinfo=None)
                to_write = []
                for bucket_ts, member_bars in sorted(buckets.items()):
                    end = bucket_ts + timedelta(hours=4)
                    # Only write if the bucket has at least 2 bars (prevents
                    # fake bars from a single sparse/gappy 1h bar being
                    # incorrectly floored into a lone "4h bar") — EXCEPT the
                    # 16:00-19:59 bucket, kept at a 1-bar minimum for
                    # backwards compatibility with days ingested before
                    # 2026-09-17: back when 1h was regular-session-only, the
                    # single 16:00 close bar WAS that bucket's maximum
                    # possible content, and the flat >=2 guard silently
                    # discarded it every day for every symbol until this
                    # fix — found live via "why do 4h bars only show
                    # 08:00/12:00?". Now that 1h also covers after-hours
                    # (see _resample_1h_from_1m_and_upsert), this bucket can
                    # have up to 4 members (16/17/18/19) on newer days; the
                    # lower minimum is still safe since it only relaxes,
                    # never tightens, the requirement.
                    min_required = 1 if bucket_ts.hour == 16 else 2
                    if len(member_bars) < min_required:
                        continue
                    bar = Bar(
                        symbol=symbol.upper(),
                        timeframe="4h",
                        open=member_bars[0].open,
                        high=max(b.high for b in member_bars),
                        low=min(b.low for b in member_bars),
                        close=member_bars[-1].close,
                        volume=sum(b.volume for b in member_bars),
                        timestamp=bucket_ts,
                        provider="aggregated_from_1h",
                        data_status=DataStatus.HISTORICAL if end < now else DataStatus.INCOMPLETE,
                    )
                    to_write.append(bar)

                if to_write:
                    written += upsert_bars(db, to_write)
                # Small delay between symbols to avoid bursts
                await asyncio.sleep(0.05)
            db.commit()
            if written:
                from backend.market_data.services.cache import _redis_cache
                for symbol in symbols_to_process:
                    _redis_cache.invalidate_bars_for_symbol(symbol)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return written

    # ------------------------------------------------------------------
    # 1d → 1wk aggregation (week closes Saturday 00:00 ET)
    # ------------------------------------------------------------------

    async def _resample_1d_to_1wk_and_upsert(
        self, _symbol: str | None = None, full_history: bool = False
    ) -> int:
        """Read 1d bars → aggregate to 1wk → upsert.

        1wk buckets are Monday 00:00 UTC.  A week is closed (written) when
        Saturday 00:00 ET of that week has passed.

        If ``_symbol`` is provided, resample only that symbol instead of
        ``self.symbols`` (used by backfill_service for newly added symbols).

        By default only the last ``_1WK_RESAMPLE_WINDOW_DAYS`` of 1d bars
        are scanned (see the class-level comment on
        ``_resample_1h_to_4h_and_upsert``); pass ``full_history=True`` for
        a one-time full backfill (startup).
        """
        from backend.repositories.bar_repository import upsert_bars

        symbols_to_process = [_symbol] if _symbol else self.symbols

        written = 0
        db = SessionLocal()
        try:
            for symbol in symbols_to_process:
                query = db.query(BarModel).filter(
                    and_(
                        BarModel.symbol == symbol.upper(),
                        BarModel.timeframe == "1d",
                    )
                )
                if not full_history:
                    cutoff = datetime.now(_NY_TZ).replace(tzinfo=None) - timedelta(
                        days=self._1WK_RESAMPLE_WINDOW_DAYS
                    )
                    query = query.filter(BarModel.timestamp >= cutoff)
                rows = query.order_by(BarModel.timestamp.asc()).all()
                if len(rows) < 5:
                    continue

                bars_src = [self._model_to_bar(r) for r in rows]
                buckets: dict[datetime, list[Bar]] = {}
                for bar in bars_src:
                    # ISO week: Monday 00:00 UTC.
                    # bar.timestamp is naive NY, so localize to NY tz first
                    try:
                        dt_ny = bar.timestamp.replace(tzinfo=_NY_TZ)
                        dt_utc = dt_ny.astimezone(timezone.utc)
                        monday = dt_utc - timedelta(days=dt_utc.weekday())
                        bucket_start_utc = monday.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
                    except Exception:
                        bucket_start_utc = bar.timestamp
                    if bucket_start_utc not in buckets:
                        buckets[bucket_start_utc] = []
                    buckets[bucket_start_utc].append(bar)

                # DB timestamps are naive NY. Get current NY time as naive for comparison.
                now_et = datetime.now(_NY_TZ).replace(tzinfo=None)
                to_write = []
                for bucket_ts, member_bars in sorted(buckets.items()):
                    # Saturday 00:00 ET closing threshold — derived from the
                    # member bars' own naive-NY timestamps (already ET wall
                    # time), NOT from bucket_ts. bucket_ts is a Monday 00:00
                    # UTC anchor whose ET-local date is the *previous*
                    # calendar day (Sunday evening) — converting it to ET
                    # and then adding 5 days landed on Friday 00:00 ET
                    # instead of Saturday, closing the week a day early.
                    first_ts = member_bars[0].timestamp
                    monday_et = (first_ts - timedelta(days=first_ts.weekday())).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )
                    saturday_et = monday_et + timedelta(days=5)

                    # Live in-progress week, same convention as 1d/1h/4h/
                    # sub-hour: written INCOMPLETE and refreshed each pass
                    # until Sat 00:00 ET closes it out.
                    bar = Bar(
                        symbol=symbol.upper(),
                        timeframe="1wk",
                        open=member_bars[0].open,
                        high=max(b.high for b in member_bars),
                        low=min(b.low for b in member_bars),
                        close=member_bars[-1].close,
                        volume=sum(b.volume for b in member_bars),
                        timestamp=bucket_ts,
                        provider="aggregated_from_1d",
                        data_status=DataStatus.HISTORICAL if now_et >= saturday_et else DataStatus.INCOMPLETE,
                    )
                    to_write.append(bar)

                if to_write:
                    written += upsert_bars(db, to_write)
                # Small delay between symbols to avoid bursts
                await asyncio.sleep(0.05)
            db.commit()
            if written:
                from backend.market_data.services.cache import _redis_cache
                for symbol in symbols_to_process:
                    _redis_cache.invalidate_bars_for_symbol(symbol)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return written

    async def _resample_write_loop(self, initial_delay: float = 0.0):
        """Every 2 min: resample 1m -> 2m/3m/5m/15m/30m, today's live 1d
        bar, today's 1h bars (rebuilt from 1m each pass — cheap, bounded
        to today, and continuously corrects any already-closed hour whose
        provider-sourced bar has a misaligned boundary; see
        _resample_1h_from_1m_and_upsert), plus 4h and 1wk — every
        timeframe now shows its still-forming bucket live (INCOMPLETE),
        not just the last fully-closed one, matching 1d's original
        convention. 4h/1wk read the 1h/1d bars this same pass just
        refreshed, so they run right after."""
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        while self.is_running:
            try:
                for tf in self._SUBHOUR_TFS:
                    await self._resample_and_upsert(tf, source_tf="1m")
                await self._resample_1d_live_and_upsert()
                await self._resample_1d_to_1wk_and_upsert()
                now_naive = datetime.now(_NY_TZ).replace(tzinfo=None)
                today_start = now_naive.replace(hour=4, minute=0, second=0, microsecond=0)
                if now_naive >= today_start:
                    hours = self._hour_starts_between(today_start, now_naive)
                    await self._resample_1h_from_1m_and_upsert(hour_starts=hours)
                await self._resample_1h_to_4h_and_upsert()
            except Exception as e:
                logger.error(f"Error in resample write loop: {e}")
            await self._jittered_sleep(120, jitter=10.0)

    async def _resample_1d_live_and_upsert(self) -> int:
        """Build/refresh TODAY's 1d bar from today's 1m bars.

        Runs every ~2 min (via _resample_write_loop) so "today" is visible
        in Recent Bars from market open onward instead of being hidden
        until the close — updating live as new 1m bars arrive. Before
        close, written with data_status=INCOMPLETE so callers/UI can tell
        it apart from a settled daily candle.

        At 16:02 ET, _write_1d_bars() (via _daily_write_loop) fetches the
        authoritative provider-sourced daily bar and upserts it on the
        SAME (symbol, "1d", timestamp) key — overwriting this row with the
        final OHLCV and data_status=HISTORICAL. So once the market has
        closed, this method skips any symbol that already has a bar for
        today (don't downgrade a real provider-sourced close back to our
        own 1m-derived aggregate).

        The exception: a symbol added to the watchlist AFTER 16:02 ET never
        got that day's _daily_write_loop pass — it was tracked too late —
        so without this, it would have no 1d bar for today at all until
        tomorrow's backfill catches up, even though we already have its
        full day of 1m bars sitting right here. In that case (market
        closed, no existing bar), still build one from the day's 1m data,
        but mark it HISTORICAL rather than INCOMPLETE since the session is
        genuinely over and no more data is coming for today.
        """
        from backend.repositories.bar_repository import upsert_bars

        now = datetime.now(_NY_TZ)
        if now.weekday() >= 5:
            return 0
        market_closed = now.hour >= 16

        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
        written = 0
        db = SessionLocal()
        try:
            for symbol in self.symbols:
                if market_closed:
                    existing = (
                        db.query(BarModel.id)
                        .filter(
                            and_(
                                BarModel.symbol == symbol.upper(),
                                BarModel.timeframe == "1d",
                                BarModel.timestamp == today_midnight,
                            )
                        )
                        .first()
                    )
                    if existing is not None:
                        continue  # _daily_write_loop already wrote the real close

                rows = (
                    db.query(BarModel)
                    .filter(
                        and_(
                            BarModel.symbol == symbol.upper(),
                            BarModel.timeframe == "1m",
                            BarModel.timestamp >= today_midnight,
                        )
                    )
                    .order_by(BarModel.timestamp.asc())
                    .all()
                )
                if len(rows) < 2:
                    continue  # not enough of today's session ingested yet

                bars_src = [self._model_to_bar(r) for r in rows]
                bar = Bar(
                    symbol=symbol.upper(),
                    timeframe="1d",
                    open=bars_src[0].open,
                    high=max(b.high for b in bars_src),
                    low=min(b.low for b in bars_src),
                    close=bars_src[-1].close,
                    volume=sum(b.volume for b in bars_src),
                    timestamp=today_midnight,
                    provider="live_from_1m",
                    data_status=DataStatus.HISTORICAL if market_closed else DataStatus.INCOMPLETE,
                )
                written += upsert_bars(db, [bar])
            db.commit()
            if written:
                from backend.market_data.services.cache import _redis_cache
                for symbol in self.symbols:
                    _redis_cache.invalidate_bars_for_symbol(symbol)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return written

    @staticmethod
    def _hour_starts_between(start: datetime, end: datetime) -> list[datetime]:
        """Every :00-aligned hour bucket from ``start`` through ``end``
        (both naive NY), inclusive of both boundaries' hours."""
        h = start.replace(minute=0, second=0, microsecond=0)
        end_h = end.replace(minute=0, second=0, microsecond=0)
        hours = []
        while h <= end_h:
            hours.append(h)
            h += timedelta(hours=1)
        return hours

    async def _resample_1h_from_1m_and_upsert(
        self, hour_starts: list[datetime] | None = None, _symbol: str | None = None,
    ) -> int:
        """Build/refresh 1h bars from 1m data for each bucket in
        ``hour_starts`` (naive NY, each already floored to :00). Defaults
        to just the current (in-progress) hour.

        Two roles, same mechanism:

        1. Live current-hour bar (hour_starts=None, the default): mirrors
           ``_resample_1d_live_and_upsert``'s proven pattern one level
           down — runs every ~2 min (via _resample_write_loop) so the
           in-progress hour is visible and updating, instead of 1h only
           ever showing the last fully-closed hour (found live
           2026-09-09: at 1:13pm the most recent bar was 12:00 —
           technically correct under "only closed hours", but surprising
           if you expect a live current-hour candle the way 1m provides).

        2. Correcting already-closed hours (explicit hour_starts, called
           from _resample_write_loop with today's hours, and from
           backfill_symbol_history with the full 1m retention window for
           a newly-backfilled symbol): found live the same day — Webull's
           1h endpoint (the primary source for every symbol) returns
           bars anchored at :30 (e.g. a bar timestamped 10:30 spans
           [10:30,11:30)), not the :00 anchors _normalize_1h_bar assumed
           only Alpaca/Webull would use. That function's existing
           "floor :30 bars to the preceding :00" rule — written assuming
           :30 bars are rare, yfinance-only fallback data — was silently
           mislabeling every Webull-sourced 1h bar: a bar covering
           [10:30,11:30) got floored and stored as "10:00", so its
           high/low (which can easily reflect the LATTER half of that
           window) end up attributed to the wrong hour entirely. There's
           no correct floor-or-ceiling fix for that — the bar genuinely
           straddles two canonical hours. The actual fix: stop trusting
           any provider's own hourly boundaries and build 1h directly
           from our own already-verified, unambiguously-timestamped 1m
           data instead, whenever 1m coverage exists (i.e. within
           RETENTION_TF_1M_DAYS). Hours older than that still rely on
           the provider as before — we don't retain 1m that far back.

        Written with data_status=INCOMPLETE if the bucket's hour hasn't
        closed yet (only ever true for the current-hour case), else
        HISTORICAL. Either way this upserts onto the SAME
        (symbol, "1h", hour_start) key _1h_write_loop/_gapfill_1h_loop
        write to — for the live case, the real bar naturally overwrites
        this one once the hour closes and a fresh fetch runs; for the
        correction case, THIS call is the one doing the overwriting, and
        that's the point.

        Spans premarket/regular/after-hours 1m data, matching 1d's live
        pre-close bar (``_resample_1d_live_and_upsert``) — changed
        2026-09-17 at the user's request so 1h/4h behave like 1d instead
        of only ever reflecting the 9:30-16:00 regular session. This is a
        deliberate departure from every OTHER 1h/4h/1d/1wk source query in
        this codebase (see BarModel.session's docstring, still accurate
        for those) — only this live/current-bucket builder spans all
        sessions now. Pre-existing HISTORICAL 1h rows from before this
        change stay regular-session-only; only today (and any symbol
        backfilled after this change) gets the wider session coverage.
        """
        from backend.repositories.bar_repository import upsert_bars

        now = datetime.now(_NY_TZ)
        if now.weekday() >= 5:
            return 0
        now_naive = now.replace(tzinfo=None)

        if hour_starts is None:
            hour_starts = [now_naive.replace(minute=0, second=0, microsecond=0)]

        symbols_to_process = [_symbol] if _symbol else self.symbols
        written = 0
        db = SessionLocal()
        try:
            for symbol in symbols_to_process:
                for hour_start in hour_starts:
                    hour_end = hour_start + timedelta(hours=1)
                    rows = (
                        db.query(BarModel)
                        .filter(
                            and_(
                                BarModel.symbol == symbol.upper(),
                                BarModel.timeframe == "1m",
                                BarModel.timestamp >= hour_start,
                                BarModel.timestamp < hour_end,
                            )
                        )
                        .order_by(BarModel.timestamp.asc())
                        .all()
                    )
                    if len(rows) < 2:
                        continue  # not enough of this hour ingested

                    bars_src = [self._model_to_bar(r) for r in rows]
                    bar = Bar(
                        symbol=symbol.upper(),
                        timeframe="1h",
                        open=bars_src[0].open,
                        high=max(b.high for b in bars_src),
                        low=min(b.low for b in bars_src),
                        close=bars_src[-1].close,
                        volume=sum(b.volume for b in bars_src),
                        timestamp=hour_start,
                        provider="live_from_1m",
                        data_status=(
                            DataStatus.INCOMPLETE if hour_end > now_naive
                            else DataStatus.HISTORICAL
                        ),
                    )
                    written += upsert_bars(db, [bar])
            db.commit()
            if written:
                from backend.market_data.services.cache import _redis_cache
                for symbol in symbols_to_process:
                    _redis_cache.invalidate_bars_for_symbol(symbol)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return written

    # ---------------------------------------------------------------------------
    # Multi-bar ingestion helpers (Phase 3.8)
    # ---------------------------------------------------------------------------

    async def _ingest_1m_recent_window(self) -> int:
        """Fetch the last ~15 min of 1m bars per symbol and write all.

        Phase 3.8: the original _ingest_bars fetched only 1 bar per cycle,
        which couldn't fill gaps of more than 1 missing bar. This method fetches
        the full recent window (range="15m") from the provider chain and writes
        every bar — upsert is keyed on (symbol, timeframe, timestamp) so
        duplicates are harmless.

        Runs once per minute (the loop's own cadence), so range="15m" gives
        us ~15 bars of lookback to cover any mid-session gap.
        """
        bars_to_upsert: list[Bar] = []
        fresh_bars: list[tuple] = []
        db: Session = SessionLocal()
        try:
            # Use batch API — one call for all symbols instead of N separate
            # calls, which avoids burning through the 60/min Webull rate limit.
            try:
                # range_="15m" (not "5d") — matches this function's own
                # docstring/intent: a small recent-window fetch, not a
                # multi-day re-pull every ~60s tick. Restored 2026-09-09;
                # had drifted to "5d" without the surrounding comments
                # being updated, which meant every cycle re-fetched and
                # re-wrote each symbol's full 1200-bar (Webull's hard
                # cap) window just to catch the latest 1-2 bars.
                batch_bars = self.manager.get_historical_bars_batch(
                    self.symbols, "1m", range_="15m", use_cache=False,
                    # Keep the live 1m feed flowing outside RTH (premarket /
                    # after-hours). Sub-hour resampling still filters on
                    # session='regular', so 2m+/1h/1d stay RTH-only.
                    include_extended_hours=True,
                )
                for symbol, bars in batch_bars.items():
                    if bars:
                        bars = _newest_bars(bars)
                        for bar in bars:
                            bar.timeframe = "1m"
                        bars_to_upsert.extend(bars)
                        fresh_bars.extend(
                            (b.symbol, b.timeframe, b, b.timestamp) for b in bars
                        )
                        logger.debug(
                            f"Ingested {len(bars)} 1m bars for {symbol} "
                            f"(range 15m, latest={bars[-1].timestamp})"
                        )
            except Exception as e:
                logger.warning(f"Failed to ingest 1m bars via batch: {e}")
                # Fall back to per-symbol ingestion if batch fails
                for symbol in self.symbols:
                    try:
                        bars = self.manager.get_historical_bars(
                            symbol, "1m", range_="15m", use_cache=False,
                            include_extended_hours=True,
                        )
                        if bars:
                            bars = _newest_bars(bars)
                            for bar in bars:
                                bar.timeframe = "1m"
                            bars_to_upsert.extend(bars)
                            fresh_bars.extend(
                                (b.symbol, b.timeframe, b, b.timestamp) for b in bars
                            )
                            logger.debug(
                                f"Ingested {len(bars)} 1m bars for {symbol} "
                                f"(range 15m, latest={bars[-1].timestamp})"
                            )
                        await asyncio.sleep(0.3)
                    except Exception as e:
                        logger.warning(f"Failed to ingest 1m bars for {symbol}: {e}")

            if bars_to_upsert:
                from backend.repositories.bar_repository import upsert_bars
                written = upsert_bars(db, bars_to_upsert)
                logger.info(f"Ingested {written} 1m bars across {len(self.symbols)} symbols")
                db.commit()
                upserted_symbols = {b.symbol.upper() for b in bars_to_upsert}
                if upserted_symbols:
                    from backend.market_data.services.cache import _redis_cache
                    for sym in upserted_symbols:
                        _redis_cache.invalidate_bars_for_symbol(sym)
                    # Immediately resample sub-hour timeframes for symbols
                    # that just got new 1m bars, instead of relying solely
                    # on the independent ~120s _resample_write_loop tick —
                    # ties "fetch live bar" and "resample from it" together
                    # causally rather than leaving them as two
                    # unsynchronized polling timers. Narrow-window
                    # (full_history=False, the default) so this stays
                    # cheap per tick; _resample_write_loop still runs on
                    # its own cadence as the catch-all for symbols with no
                    # fresh 1m tick this cycle. Deliberate tradeoff: worst
                    # case (every symbol gets a new bar every tick) roughly
                    # doubles sub-hour resample query volume versus the
                    # write loop alone — acceptable at this app's scale.
                    for sym in upserted_symbols:
                        for tf in self._SUBHOUR_TFS:
                            try:
                                await self._resample_and_upsert(tf, source_tf="1m", _symbol=sym)
                            except Exception as e:
                                logger.debug(f"immediate resample failed for {sym}/{tf}: {e}")
                    # Retention pruning runs on its own slower loop
                    # (_retention_prune_loop) instead of here — see that
                    # method's docstring for why.
                record_bars(len(fresh_bars))
        except Exception as e:
            logger.error(f"Error in 1m recent window ingest: {e}")
            db.rollback()
            fresh_bars = []
        finally:
            db.close()

        # Dispatch to in-memory engines
        from backend.models.market_data_sql import BarModel as _BM2
        for sym, tf, bar, ts in fresh_bars:
            try:
                engine_registry.dispatch_bar(
                    symbol=sym,
                    timeframe=tf,
                    price=float(bar.close or 0.0),
                    volume=int(bar.volume or 0),
                    timestamp=bar.timestamp,
                    high=getattr(bar, "high", None),
                    low=getattr(bar, "low", None),
                    open_price=getattr(bar, "open", None),
                )
            except Exception as e:
                logger.debug(f"dispatch_bar failed for {sym}/{tf}: {e}")
        return len(bars_to_upsert)

    async def _fetch_bars_with_fallback(
        self,
        symbol: str,
        timeframe: str,
        range_: str,
        stale_threshold: timedelta,
        normalize_fn,
    ) -> tuple[list[Bar], object]:
        """Shared primitive behind ``_fetch_1h_bars_with_fallback`` and
        ``_fetch_1d_bars_with_fallback``.

        Extracted 2026-09-09: the two were confirmed to be the exact same
        logic (fetch primary → normalize → check freshness → merge
        fallback if stale) with only the timeframe string, threshold, and
        normalizer varying. Before this, a fix applied to one had to be
        separately rediscovered and reapplied to the other — which is
        literally what happened: the "non-empty-but-stale primary blocks
        fallback" bug was fixed for 1h, then found independently, still
        present, in 1d.

        The naive version of this (used by both ``_write_1h_recent_window``
        and ``_gapfill_1h_once`` until 2026-09-08) only tried the fallback
        chain when the primary returned an EMPTY list — ``if not bars:``.
        That silently starves the bar whenever the primary is degraded but
        not fully down: e.g. Webull occasionally downgrades its M60
        response to M30 granularity with a short lookback (observed live:
        5 bars, none newer than ~2h old) — a non-empty response that
        satisfied ``if not bars`` and permanently blocked Alpaca/yfinance
        from ever supplying fresher data. Now: the primary's bars are
        kept, but if the freshest one is older than ``stale_threshold``,
        the fallback chain also runs and its bars are MERGED in (by
        timestamp) rather than replacing the primary's — so a partial
        primary response doesn't lose whatever good data it did have.

        ``normalize_fn`` (``_normalize_1h_bar`` / ``_normalize_1d_bar``)
        floors each bar's timestamp onto this timeframe's canonical DB
        anchor so different providers' conflicting conventions (e.g.
        webull's 1d bars at 00:00 vs yahoo_finance's at 09:30) collide on
        the same unique key instead of silently duplicating every period
        a fallback ever touched — see ``_normalize_1d_bar``'s docstring
        for the 63%-of-all-1d-rows incident this caused.

        Returns ``(bars, source)`` where ``source`` is the primary provider
        instance if only the primary was used, else ``None`` (mixed/fallback
        source — matches ``normalize_fn``'s call convention).
        """
        from backend.market_data.services.manager import (
            get_backfill_primary_provider,
            get_1h_1d_fallback_providers,
        )

        provider = get_backfill_primary_provider(timeframe)
        raw_bars: list[Bar] = []
        if provider is not None:
            # to_thread: provider.get_historical_bars is a synchronous
            # (blocking) network call. Called directly it would block this
            # coroutine's event loop — the SAME loop the 1m/quote/resample
            # ingestion loops run on — for the full HTTP round-trip, once
            # per symbol per call. That's the identical class of bug
            # documented on the process-lifetime provider cache above
            # (WebullProvider() construction blocking the shared loop for
            # hundreds of ms to seconds, delaying bars by 1-2+ minutes) —
            # this is the same failure mode, just from the fetch call
            # instead of the constructor.
            raw_bars = await asyncio.to_thread(
                provider.get_historical_bars, symbol, timeframe, range_=range_
            )
        primary_name = provider.__class__.__name__ if provider else "primary"
        bars = [
            n for n in (normalize_fn(b, primary_name) for b in raw_bars)
            if n is not None
        ]

        latest_ts = max((b.timestamp for b in bars), default=None)
        now_ny = datetime.now(_NY_TZ).replace(tzinfo=None)
        is_stale = latest_ts is None or (now_ny - latest_ts) > stale_threshold

        if is_stale:
            for fb_name in get_1h_1d_fallback_providers(timeframe):
                fb = _instantiate_backfill_provider(fb_name)
                if fb is None:
                    continue
                fb_raw = await asyncio.to_thread(
                    fb.get_historical_bars, symbol, timeframe, range_=range_
                )
                fb_bars = [
                    n for n in (normalize_fn(b, fb_name) for b in fb_raw)
                    if n is not None
                ]
                if fb_bars:
                    by_ts = {b.timestamp: b for b in bars}
                    by_ts.update({b.timestamp: b for b in fb_bars})
                    bars = list(by_ts.values())
                    provider = None  # mixed source — normalize_fn gets "fallback"
                    break

        return bars, provider

    async def _fetch_1h_bars_with_fallback(
        self, symbol: str, range_: str = "5d"
    ) -> tuple[list[Bar], object]:
        """Fetch 1h bars for ``symbol`` via BACKFILL_1H_* chain. See
        ``_fetch_bars_with_fallback`` for the freshness-aware fallback and
        normalization rationale.

        ``range_`` defaults to "5d" (enough lookback for the write/gap-fill
        loops to catch up on a short gap) — pass a wider window like "1y"
        when bootstrapping a brand-new symbol that has no history at all.
        """
        return await self._fetch_bars_with_fallback(
            symbol, "1h", range_, self._STALE_1H_THRESHOLD, _normalize_1h_bar
        )

    # How stale the primary 1h provider's freshest bar can be before the
    # fallback chain is also consulted. Generous enough to tolerate normal
    # ingestion lag (write loop fires at :02 past the hour, gap-fill every
    # 30 min) while still catching a genuinely degraded/rate-limited primary.
    _STALE_1H_THRESHOLD = timedelta(hours=2)

    async def _fetch_1d_bars_with_fallback(
        self, symbol: str, range_: str = "30d"
    ) -> tuple[list[Bar], object]:
        """Fetch 1d bars for ``symbol`` via BACKFILL_1D_* chain. See
        ``_fetch_bars_with_fallback`` for the freshness-aware fallback and
        normalization rationale.
        """
        return await self._fetch_bars_with_fallback(
            symbol, "1d", range_, self._STALE_1D_THRESHOLD, _normalize_1d_bar
        )

    # Generous vs. 1h's threshold — daily bars only settle once per session
    # and can lag over a weekend, so "stale" needs more room before it's
    # actually suspicious.
    _STALE_1D_THRESHOLD = timedelta(days=3)

    async def _write_1h_recent_window(self) -> int:
        """Fetch latest 1h bars via BACKFILL_1H_* chain and write.

        Resolves BACKFILL_1H_PRIMARY / BACKFILL_1H_FALLBACK from .env via
        get_backfill_primary_provider() / get_1h_1d_fallback_providers().
        See ``_fetch_1h_bars_with_fallback`` for the freshness-aware
        fallback logic.

        Timestamp normalization: clean :00 bars are kept as-is; Webull's 30-min
        offset bars are floored to the preceding :00 so they slot into the DB's
        (symbol, timeframe, timestamp) unique key correctly. Bars with other
        offsets are skipped.
        """
        from backend.repositories.bar_repository import upsert_bars

        written = 0
        bars_to_upsert: list[Bar] = []
        db: Session = SessionLocal()
        try:
            for symbol in self.symbols:
                try:
                    bars, provider = await self._fetch_1h_bars_with_fallback(symbol)

                    for b in bars:
                        normalized = _normalize_1h_bar(b, provider.__class__.__name__ if provider else "fallback")
                        if normalized is not None:
                            normalized.timeframe = "1h"
                            bars_to_upsert.append(normalized)
                    logger.debug(f"1h ingest: got {len(bars)} bars for {symbol}")
                    await asyncio.sleep(0.3)
                except Exception as e:
                    logger.debug(f"1h fetch failed for {symbol}: {e}")

            if bars_to_upsert:
                written = upsert_bars(db, bars_to_upsert)
                db.commit()
                if written:
                    logger.info(f"1h ingest: wrote {written} bars across {len(self.symbols)} symbols")
                    from backend.market_data.services.cache import _redis_cache
                    for sym in {b.symbol.upper() for b in bars_to_upsert}:
                        _redis_cache.invalidate_bars_for_symbol(sym)
        finally:
            db.close()
        return written

    async def _1h_write_loop(self, initial_delay: float = 0.0):
        """At :02 ET every hour: fetch 3h of 1h bars per symbol and write all.

        Fires at :02 past each hour so the closed hour-bar has settled.
        """
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        while self.is_running:
            try:
                ny = datetime.now(_NY_TZ)
                if ny.minute == 2 and ny.second < 10:
                    await self._write_1h_recent_window()
            except Exception as e:
                logger.error(f"Error in 1h write loop: {e}")
            await self._jittered_sleep(300, jitter=15.0)

    async def _gapfill_1h_loop(self, initial_delay: float = 0.0):
        """Phase 3.8 — auto gap-fill for 1h bars.

        Runs every 30 min, 24/7. For each watched symbol, fetches the last
        5 days of 1h bars via Alpaca primary + yfinance gap-fill (for 16:00 ET
        close bar that Alpaca free tier misses for less-liquid symbols) and
        writes all of them. Only full-hour boundaries (minute==0) are kept.

        Why every 30 min: the 16:00 ET close bar arrives ~20:00 UTC via
        yfinance; a 30-min cadence catches it within 30 min of arrival.
        Combined with _1h_write_loop (hourly at :02), the 16:00 bar is
        written no later than ~30 min after close.

        Why 24/7: yfinance serves extended-hours bars at any time, so
        gap-fill is useful even after RTH closes.
        """
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        while self.is_running:
            try:
                await self._gapfill_1h_once()
            except Exception as e:
                logger.error(f"Error in gap-fill 1h loop: {e}")
            await self._jittered_sleep(1800, jitter=30.0)  # 30 min ± 30s

    async def _gapfill_1h_once(self) -> int:
        """Fetch 5d of 1h bars via BACKFILL_1H_* chain and write.

        Resolves BACKFILL_1H_PRIMARY / BACKFILL_1H_FALLBACK from .env via
        get_backfill_primary_provider() / get_1h_1d_fallback_providers().
        See ``_fetch_1h_bars_with_fallback`` for the freshness-aware
        fallback logic.

        Timestamp normalization: clean :00 bars are kept as-is; Webull's 30-min
        offset bars are floored to the preceding :00 so the merge against any
        existing rows is unambiguous on (symbol, timestamp). Bars with other
        offsets are skipped.
        """
        from backend.repositories.bar_repository import upsert_bars

        written_total = 0
        bars_to_upsert: list[Bar] = []
        db = SessionLocal()
        try:
            for symbol in self.symbols:
                try:
                    bars, provider = await self._fetch_1h_bars_with_fallback(symbol)

                    for b in bars:
                        normalized = _normalize_1h_bar(b, provider.__class__.__name__ if provider else "fallback")
                        if normalized is not None:
                            normalized.timeframe = "1h"
                            bars_to_upsert.append(normalized)
                    await asyncio.sleep(0.3)
                except Exception as e:
                    logger.debug(f"1h gap-fill fetch failed for {symbol}: {e}")

            if bars_to_upsert:
                written_total = upsert_bars(db, bars_to_upsert)
                db.commit()
                if written_total:
                    logger.info(
                        f"gap-fill 1h: wrote {written_total} bars across "
                        f"{len(self.symbols)} symbols"
                    )
                    from backend.market_data.services.cache import _redis_cache
                    for sym in {b.symbol.upper() for b in bars_to_upsert}:
                        _redis_cache.invalidate_bars_for_symbol(sym)
        finally:
            db.close()

        return written_total

    async def _write_4h_bars(self, force: bool = False) -> int:
        """Aggregate 1h → 4h (NY market hours).

        When ``force=True`` (e.g. startup), the time-of-day guard is skipped
        so 4h bars are populated immediately.  When ``force=False`` (scheduled
        loop), only fires near a 4h boundary to avoid duplicate writes.
        """
        if not force:
            ny = datetime.now(_NY_TZ)
            if ny.hour % 4 != 0 or ny.minute > 2:
                return 0
        return await self._resample_1h_to_4h_and_upsert(full_history=force)

    async def _4h_write_loop(self, initial_delay: float = 0.0):
        """At :02 ET every 4h (00:02, 04:02, 08:02, 12:02, 16:02, 20:02):
        resample 1h → 4h for the just-closed 4h bucket.

        _resample_write_loop now also calls _resample_1h_to_4h_and_upsert
        every ~2 min for the live in-progress bucket, which naturally
        settles a closed bucket to HISTORICAL on its own too — this loop
        is a redundant backstop at this point (harmless, same idempotent
        upsert key), kept as a second guarantee rather than removed.
        """
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        while self.is_running:
            try:
                ny = datetime.now(_NY_TZ)
                if ny.hour % 4 == 0 and ny.minute == 2 and ny.second < 10:
                    await self._write_4h_bars()
            except Exception as e:
                logger.error(f"Error in 4h write loop: {e}")
            await self._jittered_sleep(300, jitter=15.0)

    async def _write_1d_bars(self) -> int:
        """Fetch recent 1d bars via BACKFILL_1D_* chain and write.

        See ``_fetch_1d_bars_with_fallback`` for the freshness-aware
        fallback and provider-timestamp normalization. The ON CONFLICT
        upsert is idempotent for already-settled historical bars — only
        today's bar is new. Drop 13:30 noise from Alpaca free tier
        (normalization already filters this out, but the check is kept as
        a harmless second line of defense).
        """
        from backend.repositories.bar_repository import upsert_bars

        written = 0
        bars_to_upsert: list[Bar] = []
        db = SessionLocal()
        try:
            for symbol in self.symbols:
                try:
                    bars, _provider = await self._fetch_1d_bars_with_fallback(symbol, range_="30d")

                    for b in bars:
                        # Drop 13:30 ET noise from Alpaca free tier.
                        if b.timestamp.hour == 13 and b.timestamp.minute == 30:
                            continue
                        b.timeframe = "1d"
                        bars_to_upsert.append(b)
                    await asyncio.sleep(0.3)
                except Exception as e:
                    logger.debug(f"1d fetch failed for {symbol}: {e}")

            if bars_to_upsert:
                written = upsert_bars(db, bars_to_upsert)
                db.commit()
                logger.info(f"1d ingest: wrote {written} bars across {len(self.symbols)} symbols")
                from backend.market_data.services.cache import _redis_cache
                for sym in {b.symbol.upper() for b in bars_to_upsert}:
                    _redis_cache.invalidate_bars_for_symbol(sym)
        finally:
            db.close()
        return written

    async def _daily_write_loop(self, initial_delay: float = 0.0):
        """At 16:02 ET: write 1d bar + aggregate 1wk from 1d."""
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        while self.is_running:
            try:
                ny = datetime.now(_NY_TZ)
                if ny.hour == 16 and ny.minute == 2 and ny.second < 10:
                    await self._write_1d_bars()
                    await self._resample_1d_to_1wk_and_upsert()
            except Exception as e:
                logger.error(f"Error in daily write loop: {e}")
            await self._jittered_sleep(300, jitter=15.0)

    async def _gapfill_1m_loop(self, initial_delay: float = 0.0):
        """Phase 3.8 — auto gap-fill for 1m bars.

        Runs every 2 minutes during market hours. For each watched symbol,
        fetches the last 1 day of 1m bars via the existing tier-1
        backfill (Alpaca + yfinance gap-fill) and writes only the bars
        whose timestamp is newer than the DB's latest row.

        Why every 2 min:
          - Catches mid-day gaps from provider hiccups within 2 min.
          - Catches late-arriving Alpaca data once the 15-min lag elapses.
          - Lower cadence than the 1m ingest loop (60s) so it doesn't
            compete for provider rate-limit headroom.

        Why only during market hours:
          - Outside RTH no new bars are generated, so the loop is a no-op
            and just adds provider load. We gate on 09:30-16:00 ET weekdays.
        """
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        while self.is_running:
            try:
                ny = datetime.now(_NY_TZ)
                # 09:30-16:00 ET, Mon-Fri (RTH only)
                in_rth = (
                    ny.weekday() < 5
                    and (ny.hour > 9 or (ny.hour == 9 and ny.minute >= 30))
                    and ny.hour < 16
                )
                if in_rth:
                    await self._gapfill_1m_once()
            except Exception as e:
                logger.error(f"Error in gap-fill 1m loop: {e}")
            await self._jittered_sleep(120, jitter=10.0)

    async def _gapfill_1m_once(self) -> int:
        """Run a single gap-fill pass for all watched symbols.

        For each symbol, queries the DB for the latest 1m timestamp,
        then calls _fetch_tier1_1m_bars for the last 1 day and upserts
        only the bars newer than that timestamp. Returns the total
        number of bars written across all symbols.
        """
        from backend.models.market_data_sql import BarModel as _BarModel
        from backend.market_data.services.backfill_service import (
            _fetch_tier1_1m_bars,
            _write_bars_in_chunks,
        )
        from sqlalchemy import func as _func

        written_total = 0
        for symbol in self.symbols:
            try:
                # Find the DB's latest 1m bar for this symbol.
                db = SessionLocal()
                try:
                    latest_ts = (
                        db.query(_func.max(_BarModel.timestamp))
                        .filter(
                            _BarModel.symbol == symbol.upper(),
                            _BarModel.timeframe == "1m",
                        )
                        .scalar()
                    )
                finally:
                    db.close()

                # If no data yet, skip — _seed_check on startup handles
                # cold-start backfills. We only fill mid-session gaps.
                if latest_ts is None:
                    continue

                # Fetch the last 1 day of 1m bars. _fetch_tier1_1m_bars
                # merges Alpaca primary + yfinance gap-fill and deduplicates
                # by timestamp. Manager and db_session are not used inside
                # that function — pass None.
                bars = await _fetch_tier1_1m_bars(
                    symbol, days=1, manager=None, db_session=None
                )
                if not bars:
                    continue

                # Only write bars strictly newer than the DB's latest row —
                # this function fetches a full day (~900-1200 bars) every
                # cycle just to find the handful that are actually missing.
                # Writing the whole fetched batch unconditionally (as this
                # previously did, contradicting this function's own
                # docstring) meant ~21 symbols x ~1000 row upserts every 2
                # min, even when nothing was actually missing — found live
                # 2026-09-16 via a steadily growing gap between "Ingested"
                # log lines (100s -> 227s) traced to this loop's redundant
                # write volume slowing down the whole DB, which in turn
                # made every other ingestion loop (and the regime engine's
                # "last tick" freshness) fall further behind each cycle.
                new_bars = [b for b in bars if b.timestamp > latest_ts]
                if not new_bars:
                    continue

                db = SessionLocal()
                try:
                    written = await _write_bars_in_chunks(db, new_bars)
                    db.commit()
                    if written:
                        logger.info(
                            f"gap-fill 1m: {symbol} wrote {written} bars "
                            f"(total fetched={len(bars)}, new={len(new_bars)}, "
                            f"range=[{new_bars[0].timestamp}..{new_bars[-1].timestamp}])"
                        )
                        from backend.market_data.services.cache import _redis_cache
                        _redis_cache.invalidate_bars_for_symbol(symbol)
                    written_total += written
                finally:
                    db.close()

                # Notify live engines (regime/trend/multitimeframe) same as
                # _ingest_1m_recent_window does. Without this, a bar that
                # only ever arrives via gap-fill (the main ingest loop
                # missed that minute for this symbol) updates the DB but
                # every live analysis engine never finds out — it just
                # sits frozen on whatever the last successfully-dispatched
                # bar was. Found live 2026-09-16: AAPL/SPY's regime
                # freshness stuck at "recent Xm ago" (once even jumping
                # backward to a 2h-old signal) while their 1m bars in the
                # DB were fully current — traced to exactly this gap:
                # gap-fill silently patched the missing minutes but never
                # dispatched them.
                for bar in new_bars:
                    try:
                        engine_registry.dispatch_bar(
                            symbol=symbol,
                            timeframe="1m",
                            price=float(bar.close or 0.0),
                            volume=int(bar.volume or 0),
                            timestamp=bar.timestamp,
                            high=getattr(bar, "high", None),
                            low=getattr(bar, "low", None),
                            open_price=getattr(bar, "open", None),
                        )
                    except Exception as e:
                        logger.debug(f"gap-fill dispatch_bar failed for {symbol}: {e}")
            except Exception as e:
                logger.warning(f"gap-fill 1m: failed for {symbol}: {e}")

        return written_total

    async def _retention_prune_loop(self, initial_delay: float = 0.0):
        """Every hour: prune every stored timeframe to its own retention window.

        Previously this ran inline inside ``_ingest_1m_recent_window``
        (i.e. on essentially every ~60s tick that wrote new 1m bars) even
        though retention windows are configured in days — thousands of
        no-op prune queries a day for the same result a much slower
        cadence gives. Hourly is still far more often than the data
        actually needs pruning; it just keeps the DB from growing
        noticeably past its retention window between passes.
        """
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        while self.is_running:
            try:
                from backend.repositories.bar_repository import prune_bars_by_retention

                db = SessionLocal()
                try:
                    # Rolling retention prune — per-timeframe windows
                    # (settings.retention), not one global cutoff. See
                    # RetentionSettings' docstring: 1m/2m/3m/5m/15m/30m
                    # (high-volume, especially with extended-hours
                    # ingestion) get a short window; 1h/4h/1d/1wk
                    # (compact regardless) get a much longer one.
                    deleted_by_tf = prune_bars_by_retention(db)
                    if deleted_by_tf:
                        logger.info(f"Rolling retention: pruned {deleted_by_tf}")
                finally:
                    db.close()
            except Exception as e:
                logger.error(f"Error in retention prune loop: {e}")
            await self._jittered_sleep(3600, jitter=60.0)

    async def _run_loops(self):
        """Run all ingestion loops until stop() is called.

        Phase 3.7/3.8 loops are staggered with small startup offsets so they
        don't all fire their first call against the provider chain at the same
        instant — that would burst-trigger 429s on the Finnhub free tier.
        Offsets are kept under 45s so the first real data still lands promptly.
        """
        tasks = [
            asyncio.create_task(self._quote_ingestion_loop(initial_delay=0.0)),
            asyncio.create_task(self._bar_ingestion_loop(initial_delay=5.0)),
            asyncio.create_task(self._status_ingestion_loop(initial_delay=10.0)),
            asyncio.create_task(self._provider_health_loop(initial_delay=15.0)),
            asyncio.create_task(self._signal_recording_loop(initial_delay=20.0)),
            asyncio.create_task(self._signal_outcome_backfill_loop(initial_delay=23.0)),
            # Phase 3.7 — multi-timeframe live ingestion + resample-at-write
            asyncio.create_task(self._resample_write_loop(initial_delay=25.0)),
            asyncio.create_task(self._1h_write_loop(initial_delay=28.0)),
            asyncio.create_task(self._4h_write_loop(initial_delay=31.0)),
            asyncio.create_task(self._daily_write_loop(initial_delay=34.0)),
            # Phase 3.8 — auto gap-fill every 5 min during RTH
            asyncio.create_task(self._gapfill_1m_loop(initial_delay=37.0)),
            asyncio.create_task(self._gapfill_1h_loop(initial_delay=39.0)),
            asyncio.create_task(self._retention_prune_loop(initial_delay=42.0)),
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Ingestion loops cancelled")
        except Exception as e:
            logger.error(f"Error in ingestion service: {e}")
            raise

    def stop(self):
        """Stop the ingestion service.

        Signals the background loop to cancel and waits for the thread to exit.
        """
        if not self.is_running:
            return
        logger.info("Stopping market data ingestion service")
        self.is_running = False
        set_ingestion_running(False)
        # If the background thread is still running, ask the event loop to
        # cancel its tasks. We use ``call_soon_threadsafe`` because the
        # loop is owned by a different thread.
        if self._loop is not None and not self._loop.is_closed():

            def _cancel_all():
                for task in asyncio.all_tasks(self._loop):
                    task.cancel()

            try:
                self._loop.call_soon_threadsafe(_cancel_all)
            except RuntimeError:
                # Loop already shut down — nothing to cancel.
                pass
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def register_symbol(self, symbol: str) -> None:
        """Start live-tracking ``symbol`` immediately — no backfill triggered.

        This is the ONLY thing that has to happen synchronously for a
        symbol to start getting quotes/1m bars from the next loop tick:
        append it to ``self.symbols`` and seed its tracking dicts so the
        throttle checks (``last_quote_update`` etc.) don't skip it on the
        very first tick. Idempotent — calling this for an already-tracked
        symbol is a no-op.

        Historical backfill is a separate, decoupled concern — see
        ``backend.market_data.services.backfill_queue.enqueue_backfill`` —
        so a symbol shows up in *live* data within the next ~30-60s
        regardless of whether/when its backfill job actually runs. This
        split replaced ``_new_symbol_bootstrap``, which used to bundle
        "start tracking" together with a second, independent backfill
        attempt that raced the watchlist router's own trigger for the
        same symbol on two different event loops (see
        ``backfill_service.py``'s module docstring for the incident that
        caused — SOFI backfilling with only 1m+1h data after both
        attempts crashed on a lock bound to the wrong loop).

        Also (re-)subscribes ``symbol`` on the Webull MQTT stream, which is
        wholly independent of the REST-tracking state below and always
        attempted, not just on first add: it's the ONLY thing that feeds
        ``backend.tape.tape_engine.TapeEngine`` (see
        ``backend/market_data/streaming/bridge.py``'s module docstring).
        Without this, a symbol added directly via the watchlist endpoints
        (as opposed to being present in the one watchlist
        ``ingestion_service`` loads at startup and subscribes via
        ``main.py``'s lifespan) got REST-polled quotes/bars but never a
        live trade tick — its Tape Pressure card would show a one-time
        historical seed that ages out of the rolling window and then stays
        permanently empty. A re-enabled symbol needs this same (re-)wake-up
        too: disabling a symbol doesn't remove it from ``self.symbols`` or
        unsubscribe it, but it was never subscribed in the first place if
        it was disabled before ever being live-subscribed — so this must
        run unconditionally, ahead of the REST-tracking early-return below
        (subscribing twice is a harmless no-op; ``WebullStreamClient.
        subscribe()`` dedups against its own subscribed-set).
        """
        symbol = symbol.upper()
        try:
            from backend.market_data.streaming.webull_stream import get_webull_stream_client

            _stream = get_webull_stream_client()
            if _stream is not None:
                _stream.subscribe([symbol])
        except Exception:  # noqa: BLE001
            pass

        if symbol in self.symbols:
            return
        self.symbols.append(symbol)
        self.last_quote_update[symbol] = datetime.min
        self.last_status_update[symbol] = datetime.min
        self.last_bar_update[symbol] = {"1m": datetime.min}
        logger.info(f"register_symbol: {symbol} now live-tracked ({len(self.symbols)} total)")

    def refresh_symbols_from_watchlist(self) -> list[str]:
        """Reload the symbol list from the active watchlist.

        Adds new symbols (via ``register_symbol`` — live tracking only,
        see that method's docstring for why backfill is NOT triggered
        from here) and removes symbols that are no longer in any
        watchlist. Called by the watchlist delete/symbol-remove endpoints
        so the ingestion loops stop fetching deleted symbols immediately,
        and by ``POST /api/market-data/ingestion/symbols/refresh`` for any
        out-of-band watchlist change.

        Historical backfill for a newly ADDED symbol is triggered
        separately, by the watchlist router itself at add time
        (``backend.market_data.services.backfill_queue.enqueue_backfill``)
        — not from here, and not from this method's removed
        ``_new_symbol_bootstrap`` call (see ``register_symbol``'s
        docstring).

        Returns the new (full) symbol list. If the service is not running,
        only updates ``self.symbols`` and the tracking dicts.
        """
        new_symbols = self._load_symbols_and_watchlists_from_all_active_watchlists()[0]
        old_set = set(self.symbols)
        new_set = set(new_symbols)
        added = new_set - old_set
        removed = old_set - new_set
        for symbol in added:
            self.register_symbol(symbol)
        self.symbols = new_symbols
        # Keep the Webull MQTT subscription set in sync with the watchlist.
        try:
            from backend.market_data.streaming.webull_stream import get_webull_stream_client

            _stream = get_webull_stream_client()
            if _stream is not None:
                if added:
                    _stream.subscribe(added)
                if removed:
                    _stream.unsubscribe(removed)
        except Exception:  # noqa: BLE001
            pass
        logger.info(f"Refreshed symbols: {len(new_symbols)} total, {len(added)} new ({list(added)})")
        return self.symbols

    async def _jittered_sleep(self, base_seconds: float, jitter: float = 1.0) -> None:
        """Sleep for ``base_seconds ± jitter`` to decorrelate loop timing.

        When multiple loops share the same base interval (e.g. 60s) they
        naturally synchronise over time. Adding random jitter breaks the
        phase-lock so concurrent loops spread their request bursts across
        the provider's rate-limit window rather than landing in the same
        second every minute.
        """
        offset = random.uniform(-jitter, jitter)
        await asyncio.sleep(max(0, base_seconds + offset))

    async def _startup_resample_tiers(self) -> None:
        """One-time resample of 4h and 1wk bars at startup.

        After _seed_check completes (backfill of 1m/1h/1d), immediately
        aggregate 1h → 4h and 1d → 1wk so these timeframes are populated
        before the first scheduled loop fire.  Without this, 4h bars are
        absent until the next :02 4h boundary and 1wk bars until 16:02 ET
        — which could be hours after server restart.
        """
        # 4h: use force=True to bypass time-of-day guard.
        try:
            written_4h = await self._write_4h_bars(force=True)
            logger.info(f"startup 4h aggregation: wrote {written_4h} bars")
        except Exception as e:
            logger.error(f"startup 4h aggregation failed: {e}", exc_info=True)

        # 1wk: aggregate from 1d using dedicated method.
        try:
            written_1wk = await self._resample_1d_to_1wk_and_upsert(full_history=True)
            logger.info(f"startup 1wk aggregation: wrote {written_1wk} bars")
        except Exception as e:
            logger.error(f"startup 1wk aggregation failed: {e}", exc_info=True)

    async def _seed_check(self) -> None:
        """Phase 3.3.16 — backfill symbols whose history is missing or stale.

        On startup, for each watched symbol, check whether the oldest bar in
        the DB is more than 700 days old (i.e. effectively missing). If so,
        enqueue a backfill job. This is the "seed" path: the first run after
        a fresh DB, or after switching retention, picks up the missing
        history.

        Routes through ``backfill_queue.enqueue_backfill`` — NOT a direct
        call to ``backfill_symbol_history`` (that was this method's
        original, Phase-3.3.16-era behavior, via a since-removed
        ``_safe_backfill`` helper). A direct call bypassed the RQ pipeline's
        single-flight guard and worker-count concurrency cap entirely,
        which reopened exactly the "two uncoordinated callers of
        backfill_symbol_history on two different event loops" class of bug
        (the 2026-09-09 SOFI incident) the RQ-based redesign eliminated
        everywhere else — a symbol added right before a restart could get
        this startup path AND the RQ worker's job racing for it. Found via
        a 2026-09-08 post-redesign completeness audit. ``enqueue_backfill``
        already no-ops if a backfill for the symbol is confirmed in flight,
        so this is safe to call unconditionally.
        """
        from datetime import timedelta as _td
        from sqlalchemy import func as _func
        from backend.models.market_data_sql import BarModel as _BarModel
        from backend.market_data.services.backfill_queue import enqueue_backfill

        threshold = datetime.now() - _td(days=700)
        symbols = list(self.symbols)
        if not symbols:
            return
        for symbol in symbols:
            try:
                db = SessionLocal()
                try:
                    oldest = (
                        db.query(_func.min(_BarModel.timestamp))
                        .filter(_BarModel.symbol == symbol.upper())
                        .scalar()
                    )
                finally:
                    db.close()
                # No data at all, or data older than the seed threshold.
                if oldest is None or oldest < threshold:
                    logger.info(
                        f"_seed_check: enqueueing backfill for {symbol} "
                        f"(oldest={oldest})"
                    )
                    # enqueue_backfill is sync (Redis + a couple of small
                    # DB queries, no provider I/O) and already
                    # single-flight-checked — no asyncio.create_task or
                    # separate wrapper needed, unlike the old direct call.
                    enqueue_backfill(symbol)
            except Exception as e:
                logger.warning(f"_seed_check: failed for {symbol}: {e}")


    async def _quote_ingestion_loop(self, initial_delay: float = 0.0):
        """Continuously ingest quote data.

        ``initial_delay`` is a startup offset (seconds) used to stagger
        the first fire so concurrent loops don't burst against the
        provider chain.
        """
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        while self.is_running:
            try:
                await self._ingest_quotes()
                # 30s + jitter — break fixed-cadence patterns so the
                # provider sees a more human-like request pattern.
                await self._jittered_sleep(30, jitter=3.0)
            except Exception as e:
                logger.error(f"Error in quote ingestion loop: {e}")
                await asyncio.sleep(5)  # Short delay before retry

    async def _bar_ingestion_loop(self, initial_delay: float = 0.0):
        """Continuously ingest 1m bar data.

        Phase 3.1: ingestion is 1m-only. Higher timeframes are derived
        at read time by ``resample_ohlcv()`` in bar_repository.get_bars().

        Cycle: 8 symbols × 1 timeframe = 8 calls. With a per-call delay
        of 0.2s the cycle takes ~1.6s, which keeps us well under
        Finnhub's 60 req/sec ceiling even when the quote and health
        loops fire alongside it.
        """
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        while self.is_running:
            try:
                await self._ingest_bars()
                await self._jittered_sleep(60, jitter=5.0)
            except Exception as e:
                logger.error(f"Error in bar ingestion loop: {e}")
                await asyncio.sleep(10)

    async def _signal_recording_loop(self, initial_delay: float = 0.0):
        """Record one HistoricalSignal row per (symbol, timeframe) per bar.

        90s cadence — independent of ``_signal_outcome_backfill_loop``'s
        300s cadence (see that method's docstring for why they're split).
        """
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        while self.is_running:
            try:
                recorded = await asyncio.to_thread(
                    signal_recorder.record_from_recent_bars,
                    self.symbols,
                )
                if recorded:
                    logger.debug(f"Recorded {recorded} historical signals")
            except Exception as e:
                logger.error(f"Error in signal recording loop: {e}")

            await self._jittered_sleep(90, jitter=5.0)

    async def _signal_outcome_backfill_loop(self, initial_delay: float = 0.0):
        """Backfill forward outcomes for old signals.

        300s cadence. Was previously fired from the same loop iteration as
        ``_signal_recording_loop`` (both sharing one 60s±5s sleep at the
        end) even though the docstring documented 90s/300s as independent
        intervals — meaning ``backfill_outcomes(1000)`` actually ran ~5x
        more often than intended. Split into its own loop so each task
        runs on its documented cadence.
        """
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        while self.is_running:
            try:
                backfilled = await asyncio.to_thread(
                    signal_recorder.backfill_outcomes, 1000
                )
                if backfilled:
                    logger.debug(f"Backfilled {backfilled} signal outcomes")
            except Exception as e:
                logger.error(f"Error in signal backfill loop: {e}")

            await self._jittered_sleep(300, jitter=15.0)

    async def _status_ingestion_loop(self, initial_delay: float = 0.0):
        """Continuously ingest market status data"""
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        while self.is_running:
            try:
                await self._ingest_market_status()
                await self._jittered_sleep(300, jitter=15.0)
            except Exception as e:
                logger.error(f"Error in status ingestion loop: {e}")
                await asyncio.sleep(30)

    async def _provider_health_loop(self, initial_delay: float = 0.0):
        """Continuously monitor provider health"""
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        while self.is_running:
            try:
                await self._update_provider_health()
                await self._jittered_sleep(60, jitter=5.0)
            except Exception as e:
                logger.error(f"Error in provider health loop: {e}")
                await asyncio.sleep(10)

    async def _ingest_quotes(self):
        """Ingest latest quotes for all symbols and push to live analysis engines."""
        logger.debug("Ingesting quotes")
        db: Session = SessionLocal()
        # Collect quotes we actually fetched, so we can dispatch to engines
        # *after* the DB commit succeeds. A failure to commit shouldn't drive
        # in-memory engine state with rows we never persisted.
        fresh_quotes: list[Quote] = []
        try:
            # One batch call for the whole watchlist instead of N per-symbol
            # calls — the single biggest reducer of Webull REST load. The
            # MQTT stream is deliberately scoped to the tape only, so REST
            # remains the sole quote source for the trend/regime/scanner
            # engines. The ~10s per-symbol throttle still applies.
            now = datetime.now()
            wanted = [
                s for s in self.symbols
                if now - self.last_quote_update.get(s, datetime.min) >= timedelta(seconds=10)
            ]
            if not wanted:
                return

            try:
                # to_thread: get_batch_quotes is fully synchronous — the
                # provider batch call plus, on partial failure, a serial
                # per-symbol fallback loop (each going through the full
                # rate-limiter/circuit-breaker/retry stack). Called
                # directly it would block this coroutine's event loop —
                # shared with every other ingestion loop — for the whole
                # duration; a few consistently-failing symbols could stall
                # quote ingestion for everything else on a 30s cadence.
                quotes_map = await asyncio.to_thread(self.manager.get_batch_quotes, wanted)
            except Exception as e:
                logger.warning(f"Batch quote fetch failed: {e}")
                quotes_map = {}

            for quote in quotes_map.values():
                try:
                    quote_repository.add_quote(db, quote)
                    self.last_quote_update[quote.symbol.upper()] = now
                    fresh_quotes.append(quote)
                except Exception as e:
                    logger.warning(f"Failed to store quote for {getattr(quote,'symbol','?')}: {e}")

            db.commit()
        except Exception as e:
            logger.error(f"Error committing quotes to database: {e}")
            db.rollback()
            fresh_quotes = []  # don't dispatch rows that didn't persist
        finally:
            db.close()

        # Push fresh ticks into the in-memory analysis engines (e.g. regime)
        # after the DB write succeeded. Engine updates are sync + fast, safe
        # to call from the asyncio loop.
        #
        # The timestamp MUST be normalized before dispatch. Providers return
        # naive NY wall time; the downstream trend/timeframe engines stamp a
        # bare naive value according to their own convention. Passing the raw
        # value here is what pinned the regime signal 4-5h in the past.
        for q in fresh_quotes:
            notified = engine_registry.dispatch_quote(
                symbol=q.symbol,
                price=q.price,
                volume=q.volume or 0,
                timestamp=_ensure_aware(q.timestamp),
                # Quote objects only carry bid/ask — engines that need OHLC
                # fall back to `price` for missing fields (see regime engine).
                high=None, low=None, open_price=None,
            )
            if notified:
                logger.debug(f"Dispatched {q.symbol} quote to {notified} engine(s)")

    async def _ingest_bars(self):
        """Ingest latest bars for all symbols and push to live engines.

        Phase 3.8: delegates to _ingest_1m_recent_window which fetches the
        last 15 min of 1m bars per symbol (instead of just the latest single
        bar) and writes all of them. This catches any mid-session gap that
        the live provider fills within the last 15 min.

        Fresh bars are dispatched into the in-memory trend + confluence engines
        after a successful DB commit — handled inside _ingest_1m_recent_window.
        """
        await self._ingest_1m_recent_window()

    async def _ingest_market_status(self):
        """Ingest market status for all symbols"""
        logger.debug("Ingesting market status")
        db: Session = SessionLocal()
        try:
            for symbol in self.symbols:
                # Check if we need to update
                last_update = self.last_status_update.get(symbol, datetime.min)
                if datetime.now() - last_update < timedelta(minutes=5):  # Min 5min between status updates
                    continue

                try:
                    # to_thread: get_market_status makes a blocking
                    # provider network call on a miss — same event-loop-
                    # blocking risk as the other manager calls fixed
                    # nearby (get_batch_quotes, get_historical_bars).
                    status: MarketStatus = await asyncio.to_thread(
                        self.manager.get_market_status, symbol
                    )

                    # Store in database
                    db_status = MarketStatusModel(
                        symbol=status.symbol,
                        is_open=status.is_open,
                        next_open=status.next_open,
                        next_close=status.next_close,
                        timezone=status.timezone,
                        provider=status.provider,
                        timestamp=status.timestamp
                    )
                    db.add(db_status)

                    self.last_status_update[symbol] = datetime.now()
                    logger.debug(f"Ingested market status for {symbol}: {'OPEN' if status.is_open else 'CLOSED'}")

                except Exception as e:
                    logger.warning(f"Failed to ingest market status for {symbol}: {e}")

            db.commit()
        except Exception as e:
            logger.error(f"Error committing market status to database: {e}")
            db.rollback()
        finally:
            db.close()

    async def _update_provider_health(self):
        """Update provider health status"""
        logger.debug("Updating provider health")
        db: Session = SessionLocal()
        try:
            # NOTE: market_data_manager exposes `get_provider_statuses` (plural)
            # which returns a Dict[str, ProviderStatus]. The earlier singular
            # name did not exist and the loop was silently raising once a
            # minute.
            provider_statuses = self.manager.get_provider_statuses()

            for provider_name, status in provider_statuses.items():
                # Store in database
                db_status = ProviderStatusModel(
                    provider_name=provider_name,
                    is_healthy=status.is_healthy,
                    latency_ms=status.latency_ms,
                    rate_limit_remaining=status.rate_limit_remaining,
                    last_success=status.last_success,
                    error_message=status.error_message,
                    timestamp=status.timestamp
                )
                db.add(db_status)

            db.commit()
            logger.debug("Updated provider health status")
        except Exception as e:
            logger.error(f"Error updating provider health: {e}")
            db.rollback()
        finally:
            db.close()

    def get_latest_quote(self, symbol: str) -> Quote | None:
        """Get the latest quote for a symbol from database"""
        return quote_repository.get_latest_quote(symbol)

    def get_latest_bar(self, symbol: str, timeframe: str) -> Bar | None:
        """Get the latest bar for a symbol and timeframe from database"""
        db: Session = SessionLocal()
        try:
            db_bar = db.query(BarModel)\
                .filter(and_(BarModel.symbol == symbol, BarModel.timeframe == timeframe))\
                .order_by(BarModel.timestamp.desc())\
                .first()

            if db_bar:
                try:
                    status = DataStatus(db_bar.data_status)
                except ValueError:
                    logger.warning(
                        "Unknown data_status '%s' for %s %s bar; treating as LIVE",
                        db_bar.data_status, symbol, timeframe
                    )
                    status = DataStatus.LIVE
                return Bar(
                    symbol=db_bar.symbol,
                    timestamp=db_bar.timestamp,
                    open=db_bar.open,
                    high=db_bar.high,
                    low=db_bar.low,
                    close=db_bar.close,
                    volume=db_bar.volume,
                    timeframe=db_bar.timeframe,
                    provider=db_bar.provider,
                    data_status=status
                )
            return None
        finally:
            db.close()

    def get_quote_history(self, symbol: str, limit: int = 100) -> list[Quote]:
        """Get historical quotes for a symbol"""
        return quote_repository.get_quote_history(symbol, limit)

# Global instance for easy access
ingestion_service = MarketDataIngestionService()

# Safety net: ensure the background thread stops on process exit.
atexit.register(ingestion_service.stop)
