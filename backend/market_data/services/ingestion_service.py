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
    QuoteModel,
)
from backend.observability import record_bar, record_bars, set_ingestion_running
from backend.repositories.watchlist_repository import WatchlistRepository
from backend.services.signal_recorder import signal_recorder

from .engine_seeder import engine_registry
from .engine_seeder import _ensure_aware
from .manager import MarketDataManager

logger = logging.getLogger(__name__)


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
        """Load symbols from the active watchlist in the database.

        Strategy: find the first active watchlist that has at least one
        enabled symbol. This avoids the trap where an empty newer watchlist
        shadows an older populated one.

        Returns an empty list if no watchlist has any enabled symbols.
        Ingestion simply becomes a no-op in that case — the user must add
        a symbol to a watchlist before market data will start flowing.
        """
        try:
            db = SessionLocal()
            try:
                repo = WatchlistRepository(db)
                watchlists = repo.get_watchlists(active_only=True)
                # Iterate newest-first (get_watchlists already orders this
                # way) and pick the first watchlist that has enabled symbols.
                for wl in watchlists:
                    symbols = repo.get_watchlist_symbols(wl.id, enabled_only=True)
                    if symbols:
                        symbol_list = [s.symbol.upper() for s in symbols]
                        logger.info(f"Loaded {len(symbol_list)} symbols from watchlist '{wl.name}': {symbol_list}")
                        return symbol_list
                if watchlists:
                    logger.info("Active watchlists exist but all are empty — no symbols to ingest")
                else:
                    logger.info("No active watchlist found — no symbols to ingest")
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Failed to load symbols from watchlist: {e}")
        return []

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
            self.symbols = self._load_symbols_from_watchlist()
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
    _RESAMPLE_WIDENING_HOURS: dict[str, int] = {
        "2m": 0, "3m": 0, "5m": 0, "15m": 0, "30m": 1,
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
        )

    # How many source bars to fetch per symbol per resample pass.
    # Covers enough bars for at least 5 complete target buckets + lead buffer.
    _RESAMPLE_FETCH_COUNT = 500

    async def _resample_and_upsert(self, target_tf: str, source_tf: str) -> int:
        """Read source_tf bars → resample → upsert confirmed-closed buckets.

        Phase 3.7: writes resampled bars to the DB so reads return direct
        rows instead of recomputing on every request. Only confirmed-closed
        buckets (end-time < now) are written.

        Uses a fixed bar-count fetch (desc order) rather than a time window
        so the loop still produces output after market hours.
        """
        from backend.repositories.bar_repository import upsert_bars
        from backend.utils.resampler import resample_ohlcv, ResampleError, _TF_MINUTES

        written = 0
        db = SessionLocal()
        try:
            for symbol in self.symbols:
                rows = (
                    db.query(BarModel)
                    .filter(
                        and_(
                            BarModel.symbol == symbol.upper(),
                            BarModel.timeframe == source_tf,
                        )
                    )
                    .order_by(BarModel.timestamp.desc())
                    .limit(self._RESAMPLE_FETCH_COUNT)
                    .all()
                )
                if len(rows) < 2:
                    continue
                # Ascending order for resampler.
                rows = list(reversed(rows))
                bars_src = [self._model_to_bar(r) for r in rows]
                try:
                    resampled = resample_ohlcv(bars_src, target_tf)
                except ResampleError:
                    continue

                target_mins = _TF_MINUTES.get(target_tf, 60)
                # Use naive UTC so comparisons work with bar timestamps (naive in DB).
                now = datetime.utcnow()
                to_write = []
                for bar in resampled:
                    end = bar.timestamp + timedelta(minutes=target_mins)
                    if end < now:
                        bar.provider = "aggregated"
                        to_write.append(bar)
                if to_write:
                    written += upsert_bars(db, to_write)
                    await asyncio.sleep(0.05)
            db.commit()
            if written:
                from backend.market_data.services.cache import _redis_cache
                for symbol in self.symbols:
                    _redis_cache.invalidate_bars_for_symbol(symbol)
        finally:
            db.close()
        return written

    async def _resample_write_loop(self, initial_delay: float = 0.0):
        """Every 2 min: resample 1m → 2m/3m/5m/15m/30m."""
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        while self.is_running:
            try:
                for tf in self._SUBHOUR_TFS:
                    await self._resample_and_upsert(tf, source_tf="1m")
            except Exception as e:
                logger.error(f"Error in resample write loop: {e}")
            await self._jittered_sleep(120, jitter=10.0)

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
            for symbol in self.symbols:
                try:
                    bars = self.manager.get_historical_bars(
                        symbol, "1m", range_="15m", use_cache=False
                    )
                    if bars:
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
                    await asyncio.sleep(0.3)  # slight delay per symbol
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
                    # Rolling retention prune
                    from datetime import timedelta as _td
                    from backend.repositories.bar_repository import prune_bars_older_than
                    from backend.config.settings import settings as _s
                    retention_days = _s.market_data.bar_retention_days
                    cutoff = datetime.now() - _td(days=retention_days + 1)
                    from backend.models.market_data_sql import BarModel as _BM
                    stale_count = (
                        db.query(_BM.id)
                        .filter(_BM.timestamp < cutoff)
                        .count()
                    )
                    if stale_count > 0:
                        deleted = prune_bars_older_than(db, cutoff)
                        if deleted:
                            logger.info(
                                f"Rolling retention: pruned {deleted} bars older "
                                f"than {cutoff.date()} (retention={retention_days}d)"
                            )
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

    async def _write_1h_recent_window(self) -> int:
        """Fetch latest 1h bars from Alpaca (primary) + yfinance (gap-fill) and write.

        Phase 3.8.6: Alpaca returns clean minute=0 bars but the free tier
        often misses the 16:00 close bar for less-liquid symbols (e.g. NVDA
        stops at 15:00). yfinance returns the 16:00 close using extended-hour
        timestamps (20:00 UTC = 16:00 ET). Merge: Alpaca wins, yfinance fills
        the missing 16:00 bucket. Webull is excluded (30-min offset noise).
        """
        from backend.repositories.bar_repository import upsert_bars
        from backend.market_data.providers.alpaca_provider import AlpacaProvider
        from backend.market_data.providers.yfinance_provider import YFinanceProvider

        written = 0
        bars_to_upsert: list[Bar] = []
        db: Session = SessionLocal()
        try:
            alpaca = AlpacaProvider()
            yf = YFinanceProvider()
            for symbol in self.symbols:
                try:
                    merged: dict = {}
                    # 1. Alpaca primary
                    try:
                        bars = alpaca.get_historical_bars(symbol, "1h", range_="3h")
                        for b in bars:
                            if b.timestamp.minute == 0:
                                merged[b.timestamp] = b
                    except Exception as e:
                        logger.debug(f"1h alpaca fetch failed for {symbol}: {e}")

                    # 2. yfinance gap-fill for the 16:00 ET bar
                    #    yfinance stamps the 16:00 bar as 20:00 UTC; remap
                    #    to a naive NY 16:00 for consistency with Alpaca.
                    try:
                        yf_bars = yf.get_historical_bars(symbol, "1h", range_="2d")
                        for b in yf_bars:
                            ny = b.timestamp
                            if ny.tzinfo is not None:
                                # convert to naive NY
                                ny = ny.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
                            if ny.hour == 16 and ny.minute == 0 and ny not in merged:
                                merged[ny] = Bar(
                                    symbol=b.symbol,
                                    timestamp=ny,
                                    open=b.open, high=b.high, low=b.low, close=b.close,
                                    volume=b.volume, timeframe="1h",
                                    provider=b.provider, data_status=b.data_status,
                                )
                    except Exception as e:
                        logger.debug(f"1h yfinance gap-fill failed for {symbol}: {e}")

                    bars = sorted(merged.values(), key=lambda b: b.timestamp)
                    if bars:
                        for bar in bars:
                            bar.timeframe = "1h"
                        bars_to_upsert.extend(bars)
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
                now = datetime.now(timezone.utc)
                ny = now.astimezone(ZoneInfo("America/New_York"))
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
        """Fetch 1h bars from Alpaca (primary) + yfinance (gap-fill) and write full-hour buckets.

        Phase 3.8.6: uses Alpaca directly. Webull is excluded because it returns
        1h bars at 30-minute offsets. yfinance is included as gap-fill for the
        16:00 ET close bar (Alpaca free tier often misses it for less-liquid symbols).
        """
        from backend.repositories.bar_repository import upsert_bars
        from backend.market_data.providers.alpaca_provider import AlpacaProvider
        from backend.market_data.providers.yfinance_provider import YFinanceProvider

        written_total = 0
        bars_to_upsert: list[Bar] = []
        db = SessionLocal()
        try:
            alpaca = AlpacaProvider()
            yf = YFinanceProvider()
            for symbol in self.symbols:
                try:
                    merged: dict = {}
                    # 1. Alpaca primary
                    try:
                        bars = alpaca.get_historical_bars(symbol, "1h", range_="5d")
                        for b in bars:
                            if b.timestamp.minute == 0:
                                merged[b.timestamp] = b
                    except Exception as e:
                        logger.debug(f"1h gap-fill alpaca failed for {symbol}: {e}")

                    # 2. yfinance gap-fill for 16:00 ET bar
                    try:
                        yf_bars = yf.get_historical_bars(symbol, "1h", range_="5d")
                        for b in yf_bars:
                            ny = b.timestamp
                            if ny.tzinfo is not None:
                                ny_et = ny.astimezone(ZoneInfo("America/New_York"))
                            else:
                                ny_et = ny.replace(tzinfo=ZoneInfo("America/New_York"))
                            # Store with UTC key so dedup works against Alpaca's
                            # UTC keys.  yfinance 16:00 ET bar comes in as
                            # 20:00 UTC; Alpaca returns it as 2026-09-04 00:00 UTC.
                            ny_utc = ny_et.astimezone(timezone.utc)
                            if ny_et.hour == 16 and ny_et.minute == 0 and ny_utc not in merged:
                                merged[ny_utc] = Bar(
                                    symbol=b.symbol,
                                    timestamp=ny_utc,
                                    open=b.open, high=b.high, low=b.low, close=b.close,
                                    volume=b.volume, timeframe="1h",
                                    provider=b.provider, data_status=b.data_status,
                                )
                    except Exception as e:
                        logger.debug(f"1h gap-fill yfinance failed for {symbol}: {e}")

                    bars = sorted(merged.values(), key=lambda b: b.timestamp)
                    if bars:
                        for bar in bars:
                            bar.timeframe = "1h"
                        bars_to_upsert.extend(bars)
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

    async def _write_4h_bars(self) -> int:
        """At 4h boundaries: resample 1h → 4h via _resample_and_upsert."""
        now = datetime.now(timezone.utc)
        ny = now.astimezone(ZoneInfo("America/New_York"))
        if ny.hour % 4 != 0 or ny.minute > 2:
            return 0
        return await self._resample_and_upsert("4h", source_tf="1h")

    async def _4h_write_loop(self, initial_delay: float = 0.0):
        """At :02 ET every 4h (00:02, 04:02, 08:02, 12:02, 16:02, 20:02):
        resample 1h → 4h for the just-closed 4h bucket.
        """
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        while self.is_running:
            try:
                now = datetime.now(timezone.utc)
                ny = now.astimezone(ZoneInfo("America/New_York"))
                if ny.hour % 4 == 0 and ny.minute == 2 and ny.second < 10:
                    await self._write_4h_bars()
            except Exception as e:
                logger.error(f"Error in 4h write loop: {e}")
            await self._jittered_sleep(300, jitter=15.0)

    async def _write_1d_bars(self) -> int:
        """Fetch latest 1d bar via live provider chain. Drop 13:30 noise."""
        from backend.market_data.services.manager import market_data_manager
        from backend.repositories.bar_repository import upsert_bars

        written = 0
        db = SessionLocal()
        try:
            for symbol in self.symbols:
                try:
                    bar = market_data_manager.get_latest_bar(symbol, "1d")
                    if bar and bar.close and bar.close > 0:
                        # Drop 13:30 noise from Alpaca free tier.
                        if bar.timestamp.hour == 13 and bar.timestamp.minute == 30:
                            continue
                        bar.timeframe = "1d"
                        written += upsert_bars(db, [bar])
                    await asyncio.sleep(0.2)
                except Exception as e:
                    logger.debug(f"1d fetch failed for {symbol}: {e}")
            db.commit()
            if written:
                from backend.market_data.services.cache import _redis_cache
                for symbol in self.symbols:
                    _redis_cache.invalidate_bars_for_symbol(symbol)
        finally:
            db.close()
        return written

    async def _daily_write_loop(self, initial_delay: float = 0.0):
        """At 16:02 ET: write 1d bar + resample 1wk from 1d."""
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        while self.is_running:
            try:
                now = datetime.now(timezone.utc)
                ny = now.astimezone(ZoneInfo("America/New_York"))
                if ny.hour == 16 and ny.minute == 2 and ny.second < 10:
                    await self._write_1d_bars()
                    await self._resample_and_upsert("1wk", source_tf="1d")
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
                now_utc = datetime.now(timezone.utc)
                ny = now_utc.astimezone(ZoneInfo("America/New_York"))
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

                # Write ALL fetched bars. upsert_bars is keyed on
                # (symbol, timeframe, timestamp) with ON CONFLICT DO UPDATE,
                # so this is safe — existing bars are not overwritten,
                # only missing ones are inserted.
                db = SessionLocal()
                try:
                    written = await _write_bars_in_chunks(db, bars)
                    db.commit()
                    if written:
                        logger.info(
                            f"gap-fill 1m: {symbol} wrote {written} bars "
                            f"(total fetched={len(bars)}, "
                            f"range=[{bars[0].timestamp}..{bars[-1].timestamp}])"
                        )
                        from backend.market_data.services.cache import _redis_cache
                        _redis_cache.invalidate_bars_for_symbol(symbol)
                    written_total += written
                finally:
                    db.close()
            except Exception as e:
                logger.warning(f"gap-fill 1m: failed for {symbol}: {e}")

        return written_total

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
            # Phase 3.7 — multi-timeframe live ingestion + resample-at-write
            asyncio.create_task(self._resample_write_loop(initial_delay=25.0)),
            asyncio.create_task(self._1h_write_loop(initial_delay=28.0)),
            asyncio.create_task(self._4h_write_loop(initial_delay=31.0)),
            asyncio.create_task(self._daily_write_loop(initial_delay=34.0)),
            # Phase 3.8 — auto gap-fill every 5 min during RTH
            asyncio.create_task(self._gapfill_1m_loop(initial_delay=37.0)),
            asyncio.create_task(self._gapfill_1h_loop(initial_delay=39.0)),
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

    def refresh_symbols_from_watchlist(self) -> list[str]:
        """Reload the symbol list from the active watchlist.

        Adds new symbols and removes symbols that are no longer in any
        watchlist. Called by the watchlist delete/symbol-remove endpoints
        so the ingestion loops stop fetching deleted symbols immediately.

        Phase 3.8: when new symbols are added, schedules a backfill task
        and immediately fetches the recent window (last 15 min of 1m + last
        3h of 1h bars) to fill any mid-session gaps before the gap-fill
        loop fires 5 min later.

        Returns the new (full) symbol list. If the service is not running,
        only updates ``self.symbols`` and the tracking dicts.
        """
        new_symbols = self._load_symbols_from_watchlist()
        old_set = set(self.symbols)
        new_set = set(new_symbols)
        added = new_set - old_set
        for symbol in added:
            self.last_quote_update[symbol] = datetime.min
            self.last_status_update[symbol] = datetime.min
            self.last_bar_update[symbol] = {"1m": datetime.min}
            # Phase 3.8: schedule backfill + immediate recent-window fetch
            # for each new symbol. _seed_check is a no-op for symbols that
            # already have history in the DB (checks oldest bar timestamp).
            asyncio.create_task(self._new_symbol_bootstrap(symbol))
        self.symbols = new_symbols
        logger.info(f"Refreshed symbols: {len(new_symbols)} total, {len(added)} new ({list(added)})")
        return self.symbols

    async def _new_symbol_bootstrap(self, symbol: str):
        """Phase 3.8 — bootstrap a newly-added symbol.

        Called via create_task from refresh_symbols_from_watchlist.
        Two-step:
          1. Backfill: schedule _safe_backfill for retention_days.
             This runs the tier1 (1m) + tier2 (1h) + tier3 (1d) backfills.
          2. Immediate fetch: pull the last 15 min of 1m bars and last 3h
             of 1h bars directly from the provider chain and write them.
             This catches any bars that arrived after the backfill started,
             before the gap-fill loop fires 5 min later.
        """
        try:
            from backend.config.settings import settings as _settings
            retention_days = _settings.market_data.bar_retention_days

            logger.info(f"Bootstrapping new symbol {symbol}: scheduling backfill")
            # Run the full backfill (tier1 + tier2 + tier3) in background.
            asyncio.create_task(_safe_backfill(symbol, retention_days))

            # Give backfill a moment to start, then pull recent bars directly
            # so the user sees data immediately without waiting for the 5-min
            # gap-fill tick.
            await asyncio.sleep(3)
            logger.info(f"Bootstrapping {symbol}: fetching recent 1m window")
            bars_1m = self.manager.get_historical_bars(
                symbol, "1m", range_="15m", use_cache=False
            )
            if bars_1m:
                from backend.repositories.bar_repository import upsert_bars
                db = SessionLocal()
                try:
                    for b in bars_1m:
                        b.timeframe = "1m"
                    written = upsert_bars(db, bars_1m)
                    db.commit()
                    logger.info(
                        f"Bootstrapped {symbol}: wrote {written} 1m bars "
                        f"(range=[{bars_1m[0].timestamp}..{bars_1m[-1].timestamp}])"
                    )
                finally:
                    db.close()
            logger.info(f"Bootstrapping {symbol}: fetching recent 1h window")
            bars_1h = self.manager.get_historical_bars(
                symbol, "1h", range_="3h", use_cache=False
            )
            if bars_1h:
                from backend.repositories.bar_repository import upsert_bars
                db = SessionLocal()
                try:
                    # Webull returns 1h bars at :30 offsets (RTH-centered
                    # convention) instead of the standard :00 boundaries.
                    # Filter those out before writing.
                    bars_1h_clean = [
                        b for b in bars_1h
                        if b.timestamp.minute != 30
                    ]
                    for b in bars_1h_clean:
                        b.timeframe = "1h"
                    written = upsert_bars(db, bars_1h_clean)
                    db.commit()
                    if bars_1h_clean:
                        logger.info(
                            f"Bootstrapped {symbol}: wrote {written} 1h bars "
                            f"(range=[{bars_1h_clean[0].timestamp}..{bars_1h_clean[-1].timestamp}])"
                        )
                finally:
                    db.close()
            # Invalidate cache so fresh bars are visible immediately
            from backend.market_data.services.cache import _redis_cache
            _redis_cache.invalidate_bars_for_symbol(symbol)
        except Exception as e:
            logger.warning(f"Failed to bootstrap {symbol}: {e}")

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
        resample 1h → 4h and 1d → 1wk so these timeframes are populated
        before the first scheduled loop fire.  Without this, 4h bars are
        absent until the next :02 4h boundary and 1wk bars until 16:02 ET
        — which could be hours after server restart.
        """
        for target_tf, source_tf in [("4h", "1h"), ("1wk", "1d")]:
            try:
                written = await self._resample_and_upsert(target_tf, source_tf)
                if written:
                    logger.info(
                        f"startup resample {target_tf} from {source_tf}: "
                        f"wrote {written} bars"
                    )
            except Exception as e:
                logger.warning(f"startup resample {target_tf} failed: {e}")

    async def _seed_check(self) -> None:
        """Phase 3.3.16 — backfill symbols whose history is missing or stale.

        On startup, for each watched symbol, check whether the oldest bar in
        the DB is more than 700 days old (i.e. effectively missing). If so,
        schedule a backfill. This is the "seed" path: the first run after
        a fresh DB, or after switching retention, picks up the missing
        history.

        Symbols already in the backfill queue (single-flight) are skipped.
        """
        from datetime import timedelta as _td
        from sqlalchemy import func as _func
        from backend.config.settings import settings as _settings
        from backend.models.market_data_sql import BarModel as _BarModel
        from backend.market_data.services.backfill_service import (
            backfill_symbol_history,
        )

        retention_days = _settings.market_data.bar_retention_days
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
                        f"_seed_check: scheduling backfill for {symbol} "
                        f"(oldest={oldest})"
                    )
                    # Run in the background — we don't want to block the
                    # ingestion loops on a single symbol's backfill.
                    asyncio.create_task(
                        _safe_backfill(symbol, retention_days)
                    )
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
        """Record HistoricalSignal rows + backfill forward outcomes.

        Two responsibilities, running on independent intervals:
          - Record one signal per (symbol, timeframe) per bar (90s)
          - Backfill forward outcomes for old signals (300s)
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

            try:
                backfilled = await asyncio.to_thread(
                    signal_recorder.backfill_outcomes, 1000
                )
                if backfilled:
                    logger.debug(f"Backfilled {backfilled} signal outcomes")
            except Exception as e:
                logger.error(f"Error in signal backfill loop: {e}")

            await self._jittered_sleep(60, jitter=5.0)

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
            for symbol in self.symbols:
                # Check if we need to update (respect rate limits)
                last_update = self.last_quote_update.get(symbol, datetime.min)
                if datetime.now() - last_update < timedelta(seconds=10):  # Min 10s between updates
                    continue

                try:
                    quote: Quote = self.manager.get_quote(symbol)
                    # Per-symbol delay to stay under Finnhub's 60 req/sec ceiling.
                    # 8 symbols × 0.15s = 1.2s per quote cycle — well within budget
                    # even when the bar loop (8×7×0.2s ≈ 11s) fires alongside it.
                    await asyncio.sleep(0.15)

                    # Store in database
                    db_quote = QuoteModel(
                        symbol=quote.symbol,
                        price=quote.price,
                        bid=quote.bid,
                        ask=quote.ask,
                        volume=quote.volume,
                        timestamp=quote.timestamp,
                        provider=quote.provider,
                        data_status=quote.data_status.value
                    )
                    db.add(db_quote)

                    self.last_quote_update[symbol] = datetime.now()
                    fresh_quotes.append(quote)
                    logger.debug(f"Ingested quote for {symbol}: ${quote.price}")

                except Exception as e:
                    logger.warning(f"Failed to ingest quote for {symbol}: {e}")

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
                    status: MarketStatus = self.manager.get_market_status(symbol)

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
        db: Session = SessionLocal()
        try:
            db_quote = db.query(QuoteModel)\
                .filter(QuoteModel.symbol == symbol)\
                .order_by(QuoteModel.timestamp.desc())\
                .first()

            if db_quote:
                try:
                    status = DataStatus(db_quote.data_status)
                except ValueError:
                    # Defensive: handle legacy data written with non-enum values (e.g. 'ok').
                    logger.warning(
                        "Unknown data_status '%s' for %s quote; treating as LIVE",
                        db_quote.data_status, symbol
                    )
                    status = DataStatus.LIVE
                return Quote(
                    symbol=db_quote.symbol,
                    price=db_quote.price,
                    timestamp=db_quote.timestamp,
                    provider=db_quote.provider,
                    data_status=status,
                    bid=db_quote.bid,
                    ask=db_quote.ask,
                    volume=db_quote.volume
                )
            return None
        finally:
            db.close()

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
        db: Session = SessionLocal()
        try:
            db_quotes = db.query(QuoteModel)\
                .filter(QuoteModel.symbol == symbol)\
                .order_by(QuoteModel.timestamp.desc())\
                .limit(limit)\
                .all()

            quotes = []
            for db_quote in db_quotes:
                try:
                    status = DataStatus(db_quote.data_status)
                except ValueError:
                    status = DataStatus.LIVE
                quotes.append(Quote(
                    symbol=db_quote.symbol,
                    price=db_quote.price,
                    timestamp=db_quote.timestamp,
                    provider=db_quote.provider,
                    data_status=status,
                    bid=db_quote.bid,
                    ask=db_quote.ask,
                    volume=db_quote.volume
                ))

            return quotes
        finally:
            db.close()

async def _safe_backfill(symbol: str, retention_days: int) -> None:
    """Run backfill for ``symbol``, logging but not raising on failure.

    After backfill completes, immediately record signals for the newly
    written bars so they show up in the dashboard without waiting for
    the 90s signal recording loop.
    """
    from backend.market_data.services.backfill_service import (
        backfill_symbol_history,
    )
    try:
        result = await backfill_symbol_history(symbol, days=retention_days)
        if result.get("skipped"):
            return
        logger.info(
            f"_seed_check backfill for {symbol}: "
            f"tier1={result['tier1_written']}, tier2={result['tier2_written']}"
        )
        # Record signals for the freshly backfilled bars so they appear
        # in the dashboard immediately, not on the next 90s cycle.
        # Use backfill_signals_for_symbol so all backfilled history is
        # recorded, not just the most recent bar.
        try:
            from backend.services.signal_recorder import signal_recorder
            recorded = signal_recorder.backfill_signals_for_symbol(symbol.upper())
            if recorded:
                logger.debug(
                    f"Recorded {recorded} signals for {symbol} after seed backfill"
                )
        except Exception as sig_e:
            logger.warning(f"Signal recording after seed backfill failed for {symbol}: {sig_e}")
    except Exception as e:
        logger.warning(f"_seed_check backfill for {symbol} failed: {e}")

# Global instance for easy access
ingestion_service = MarketDataIngestionService()

# Safety net: ensure the background thread stops on process exit.
atexit.register(ingestion_service.stop)
