"""
Market Data Ingestion Service
Automatically fetches and stores market data from providers
"""
import asyncio
import atexit
import logging
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
from backend.observability import record_bar, set_ingestion_running
from backend.services.signal_recorder import signal_recorder

from .engine_seeder import engine_registry
from .manager import MarketDataManager

logger = logging.getLogger(__name__)


class MarketDataIngestionService:
    """Service for automatically ingesting and storing market data"""

    def __init__(self, symbols: list[str] = None, timeframes: list[str] = None):
        """
        Initialize the ingestion service

        Args:
            symbols: List of symbols to track (default: popular stocks)
            timeframes: List of timeframes to track (default: common timeframes)
        """
        self.symbols = symbols or ["AAPL", "GOOGL", "MSFT", "TSLA", "AMZN", "NVDA", "META", "NFLX"]
        self.timeframes = timeframes or ["1m", "5m", "15m", "30m", "1h", "1d", "1wk"]
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

    def start(self):
        """Start the ingestion service in a background thread.

        This is a *sync* method so it works correctly with FastAPI's
        BackgroundTasks (which only handles sync callables). The background
        thread owns its own asyncio event loop, so the async ingestion loops
        run in isolation without blocking the request thread.
        """
        if self.is_running:
            logger.warning("Ingestion service is already running")
            return

        self.is_running = True
        set_ingestion_running(True)
        logger.info("Starting market data ingestion service in background thread")

        def _run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._run_loops())
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
        """Run all four ingestion loops until stop() is called."""
        tasks = [
            asyncio.create_task(self._quote_ingestion_loop()),
            asyncio.create_task(self._bar_ingestion_loop()),
            asyncio.create_task(self._status_ingestion_loop()),
            asyncio.create_task(self._provider_health_loop()),
            asyncio.create_task(self._signal_recording_loop()),
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

    async def _quote_ingestion_loop(self):
        """Continuously ingest quote data"""
        while self.is_running:
            try:
                await self._ingest_quotes()
                # Wait 30 seconds between quote updates (respect rate limits)
                await asyncio.sleep(30)
            except Exception as e:
                logger.error(f"Error in quote ingestion loop: {e}")
                await asyncio.sleep(5)  # Short delay before retry

    async def _bar_ingestion_loop(self):
        """Continuously ingest bar data"""
        while self.is_running:
            try:
                await self._ingest_bars()
                # Wait 60 seconds between bar updates
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Error in bar ingestion loop: {e}")
                await asyncio.sleep(10)

    async def _signal_recording_loop(self):
        """Record HistoricalSignal rows + backfill forward outcomes.

        Two responsibilities, running on independent intervals:
          - Record one signal per (symbol, timeframe) per bar (90s)
          - Backfill forward outcomes for old signals (300s)
        """
        while self.is_running:
            try:
                recorded = await asyncio.to_thread(
                    signal_recorder.record_from_recent_bars,
                    self.symbols,
                    self.timeframes,
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

            await asyncio.sleep(90)

    async def _status_ingestion_loop(self):
        """Continuously ingest market status data"""
        while self.is_running:
            try:
                await self._ingest_market_status()
                # Wait 300 seconds (5 minutes) between status updates
                await asyncio.sleep(300)
            except Exception as e:
                logger.error(f"Error in status ingestion loop: {e}")
                await asyncio.sleep(30)

    async def _provider_health_loop(self):
        """Continuously monitor provider health"""
        while self.is_running:
            try:
                await self._update_provider_health()
                # Wait 60 seconds between health checks
                await asyncio.sleep(60)
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
        try:
            for symbol in self.symbols:
                for timeframe in self.timeframes:
                    # Check if we need to update
                    last_update = self.last_bar_update.get(symbol, {}).get(timeframe, datetime.min)
                    if datetime.now() - last_update < timedelta(minutes=1):  # Min 1min between bar updates
                        continue

                    try:
                        bar: Bar = self.manager.get_latest_bar(symbol, timeframe)

                        # Store in database
                        db_bar = BarModel(
                            symbol=bar.symbol,
                            timeframe=bar.timeframe,
                            open=bar.open,
                            high=bar.high,
                            low=bar.low,
                            close=bar.close,
                            volume=bar.volume,
                            timestamp=bar.timestamp,
                            provider=bar.provider,
                            data_status=bar.data_status.value
                        )
                        db.add(db_bar)

                        # Update tracking
                        if symbol not in self.last_bar_update:
                            self.last_bar_update[symbol] = {}
                        self.last_bar_update[symbol][timeframe] = datetime.now()
                        fresh_bars.append((bar.symbol, bar.timeframe, bar))

                        logger.debug(f"Ingested {timeframe} bar for {symbol}: O:{bar.open} H:{bar.high} L:{bar.low} C:{bar.close}")

                    except Exception as e:
                        logger.warning(f"Failed to ingest {timeframe} bar for {symbol}: {e}")

            db.commit()
            # Record ingestion metrics after a successful commit.
            for _ in fresh_bars:
                record_bar()
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
                return Quote(
                    symbol=db_quote.symbol,
                    price=db_quote.price,
                    timestamp=db_quote.timestamp,
                    provider=db_quote.provider,
                    data_status=DataStatus(db_quote.data_status),
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
                    data_status=DataStatus(db_bar.data_status)
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
                quotes.append(Quote(
                    symbol=db_quote.symbol,
                    price=db_quote.price,
                    timestamp=db_quote.timestamp,
                    provider=db_quote.provider,
                    data_status=DataStatus(db_quote.data_status),
                    bid=db_quote.bid,
                    ask=db_quote.ask,
                    volume=db_quote.volume
                ))

            return quotes
        finally:
            db.close()

# Global instance for easy access
ingestion_service = MarketDataIngestionService()

# Safety net: ensure the background thread stops on process exit.
atexit.register(ingestion_service.stop)
