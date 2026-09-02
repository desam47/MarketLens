"""
Market Data Ingestion Service
Automatically fetches and stores market data from providers
"""
import asyncio
import atexit
import logging
import random
import threading
from datetime import datetime, timedelta

from sqlalchemy import and_
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
from .manager import MarketDataManager

logger = logging.getLogger(__name__)

# Symbols that are always ingested regardless of watchlist contents.
# The regime / market-context engine analyses these market-benchmark symbols
# to determine the overall market regime. Without them the Market Context
# panel stays at "unknown". They are deduplicated against the watchlist so
# adding them manually has no effect.
#
# Note: ^VIX is excluded because it is an index (not a US_STOCK) and most
# providers reject it or return stale data. SPY + QQQ alone are sufficient
# for regime classification.
_MARKET_REGIME_SYMBOLS: tuple[str, ...] = ("SPY", "QQQ")


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
            # Always include the market-regime benchmark symbols (SPY/QQQ/IWM/VIX)
            # regardless of watchlist contents. The regime engine analyses these
            # to produce the Market Context panel; without them the panel stays
            # at "unknown". Dedupe case-insensitively so user-added duplicates
            # are collapsed.
            existing = {s.upper() for s in self.symbols}
            for core in _MARKET_REGIME_SYMBOLS:
                if core.upper() not in existing:
                    self.symbols.append(core)
            logger.info(
                f"Always-included market-regime symbols: "
                f"{[s for s in self.symbols if s in _MARKET_REGIME_SYMBOLS]}"
            )
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

    async def _run_loops(self):
        """Run all four ingestion loops until stop() is called.

        Loops are staggered with small startup offsets so they don't all
        fire their first call against the provider chain at the same
        instant — that would burst-trigger 429s on the Finnhub free tier
        (60 req/sec ceiling, with our heaviest cycle burning ~75 calls
        in <2s if unthrottled). Offsets are kept under 15s so the first
        real data still lands promptly.
        """
        tasks = [
            asyncio.create_task(self._quote_ingestion_loop(initial_delay=0.0)),
            asyncio.create_task(self._bar_ingestion_loop(initial_delay=5.0)),
            asyncio.create_task(self._status_ingestion_loop(initial_delay=10.0)),
            asyncio.create_task(self._provider_health_loop(initial_delay=15.0)),
            asyncio.create_task(self._signal_recording_loop(initial_delay=20.0)),
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
        self.is_running = False
        set_ingestion_running(False)
        logger.info("Stopping market data ingestion service")
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

        Adds any new symbols (and their per-timeframe tracking dicts) to the
        running service. Symbols that were removed from the watchlist stay in
        the tracking dicts but are no longer fetched — this avoids the cost
        of tearing down and re-creating the loops.

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
            # Phase 3.1: only track 1m updates.
            self.last_bar_update[symbol] = {"1m": datetime.min}
        self.symbols = new_symbols
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
                    signal_recorder.backfill_outcomes
                )
                if backfilled:
                    logger.debug(f"Backfilled {backfilled} signal outcomes")
            except Exception as e:
                logger.error(f"Error in signal backfill loop: {e}")

            await self._jittered_sleep(90, jitter=5.0)

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
        for q in fresh_quotes:
            notified = engine_registry.dispatch_quote(
                symbol=q.symbol,
                price=q.price,
                volume=q.volume or 0,
                timestamp=q.timestamp,
                # Quote objects only carry bid/ask — engines that need OHLC
                # fall back to `price` for missing fields (see regime engine).
                high=None, low=None, open_price=None,
            )
            if notified:
                logger.debug(f"Dispatched {q.symbol} quote to {notified} engine(s)")

    async def _ingest_bars(self):
        """Ingest latest bars for all symbols/timeframes and push to live engines.

        Fresh bars are dispatched into the in-memory trend + confluence engines
        after a successful DB commit. Only "close" + volume are sent because
        those are what the engines' update() signatures accept.
        """
        logger.debug("Ingesting bars")
        db: Session = SessionLocal()
        # (symbol, timeframe) -> Bar — fresh bars to dispatch after commit
        fresh_bars: list[tuple] = []
        # Bars to bulk-upsert (handles duplicate (symbol, timeframe, timestamp)
        # via ON CONFLICT DO UPDATE in upsert_bars — get_latest_bar can return
        # the same minute bar across fetches, which would otherwise raise
        # UNIQUE constraint failed and abort the entire commit).
        bars_to_upsert: list[Bar] = []
        try:
            for symbol in self.symbols:
                # Phase 3.1: we only ingest 1m bars. Higher TFs are
                # resampled at read time, so iterating self.timeframes
                # here would issue duplicate per-TF requests per symbol.
                # last_bar_update[symbol] only ever has the "1m" key.
                last_update = self.last_bar_update.get(symbol, {}).get(
                    "1m", datetime.min
                )
                if datetime.now() - last_update < timedelta(minutes=1):
                    # Min 1min between 1m bar updates.
                    continue

                try:
                    bar: Bar = self.manager.get_latest_bar(symbol, "1m", use_cache=False)
                    # Per-call delay: 8 symbols × 1 timeframe × 0.2s = ~1.6s
                    # per bar cycle. Stays well under Finnhub's 60 req/sec
                    # ceiling; the stagger ensures the quote/health/signal loops
                    # land their requests in different seconds.
                    await asyncio.sleep(0.2)

                    # Queue for bulk upsert (handles duplicate timestamps).
                    bars_to_upsert.append(bar)

                    # Update tracking
                    if symbol not in self.last_bar_update:
                        self.last_bar_update[symbol] = {}
                    self.last_bar_update[symbol]["1m"] = datetime.now()
                    fresh_bars.append((bar.symbol, bar.timeframe, bar))

                    logger.debug(
                        f"Ingested 1m bar for {symbol}: "
                        f"O:{bar.open} H:{bar.high} L:{bar.low} C:{bar.close}"
                    )

                except Exception as e:
                    logger.warning(f"Failed to ingest 1m bar for {symbol}: {e}")

            # Bulk upsert all fetched bars in a single transaction. Using
            # upsert_bars (not db.add) prevents UNIQUE constraint failures
            # when the same minute bar is fetched twice (current-minute bars
            # collide across consecutive ingestion runs).
            if bars_to_upsert:
                from backend.repositories.bar_repository import upsert_bars
                written = upsert_bars(db, bars_to_upsert)
                logger.debug(f"Upserted {written} bars")
                # Phase 3.1: invalidate cached bar series for every symbol that
                # just received new 1m bars. Without this, the Redis cache
                # (TTL up to 3600s for 1wk) would serve stale resampled data
                # until it naturally expires.
                upserted_symbols = {b.symbol.upper() for b in bars_to_upsert}
                if upserted_symbols:
                    from backend.market_data.services.cache import _redis_cache
                    for sym in upserted_symbols:
                        _redis_cache.invalidate_bars_for_symbol(sym)
                    # Phase 3.3.13: run rolling retention prune after each
                    # successful ingest cycle. We only prune when there is at
                    # least one bar older than (retention + 1 day) so the check
                    # is cheap (indexed scan) in steady-state.
                    from datetime import timedelta as _td
                    from backend.repositories.bar_repository import prune_bars_older_than
                    from backend.config.settings import settings as _settings
                    retention_days = _settings.market_data.bar_retention_days
                    cutoff = datetime.now() - _td(days=retention_days + 1)
                    # Count how many bars are older than the prune threshold.
                    from backend.models.market_data_sql import BarModel as _BarModel
                    stale_count = (
                        db.query(_BarModel.id)
                        .filter(_BarModel.timestamp < cutoff)
                        .count()
                    )
                    if stale_count > 0:
                        deleted = prune_bars_older_than(db, cutoff)
                        if deleted:
                            logger.info(
                                f"Rolling retention: pruned {deleted} bars older "
                                f"than {cutoff.date()} (retention={retention_days}d)"
                            )
            # Record ingestion metrics after a successful commit.
            record_bars(len(fresh_bars))
        except Exception as e:
            logger.error(f"Error committing bars to database: {e}")
            db.rollback()
            fresh_bars = []
        finally:
            db.close()

        # Dispatch fresh bars to in-memory engines (trend, multitimeframe).
        # The engine_registry routes by bar:{timeframe} key.
        for symbol, timeframe, bar in fresh_bars:
            notified = engine_registry.dispatch_bar(
                symbol=symbol,
                timeframe=timeframe,
                price=bar.close,
                volume=bar.volume or 0,
                timestamp=bar.timestamp,
            )
            if notified:
                logger.debug(f"Dispatched {symbol}/{timeframe} bar to {notified} engine(s)")
            # Invalidate the trend TTL cache for this (symbol, timeframe) so
            # the next /api/trend/{symbol}/current/{timeframe} call gets a
            # fresh result instead of a stale "unknown" response from
            # before the engine had data. Lazy import to avoid a circular
            # dep: ingestion_service is imported by api.market_data_routes,
            # so importing backend.api here at module load would loop.
            try:
                from backend.api.ttl_cache import _trend_cache
                _trend_cache.pop(f"{symbol.upper()}:{timeframe}", None)
            except Exception:
                # Cache invalidation is best-effort; don't fail ingestion.
                pass

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
    """Run backfill for ``symbol``, logging but not raising on failure."""
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
    except Exception as e:
        logger.warning(f"_seed_check backfill for {symbol} failed: {e}")

# Global instance for easy access
ingestion_service = MarketDataIngestionService()

# Safety net: ensure the background thread stops on process exit.
atexit.register(ingestion_service.stop)
