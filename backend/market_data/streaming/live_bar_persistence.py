"""Non-blocking persistence for completed Webull stream candles.

MQTT callbacks must stay fast: blocking them on SQLite can delay every symbol
on the shared stream. Completed and late-revised one-minute candles therefore
land in a bounded, deduplicating queue and are written in small background
batches. The repository protects authoritative REST rows from being replaced
by the partial Nasdaq-only stream view.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Iterable
from datetime import datetime

from backend.market_data.streaming.live_bars import LiveBar, LiveBarAggregator, live_bar_aggregator
from backend.models.market_data import Bar, DataStatus

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL_SECONDS = 1.0
_MAX_PENDING_BARS = 10_000


class LiveBarPersistence:
    """Batch completed live bars into the existing historical-bar store."""

    def __init__(
        self,
        *,
        flush_interval: float = _FLUSH_INTERVAL_SECONDS,
        max_pending: int = _MAX_PENDING_BARS,
        aggregator: LiveBarAggregator | None = None,
    ) -> None:
        self._flush_interval = flush_interval
        self._max_pending = max_pending
        self._aggregator = aggregator or live_bar_aggregator
        self._pending: OrderedDict[tuple[str, str, datetime], LiveBar] = OrderedDict()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _key(bar: LiveBar) -> tuple[str, str, datetime]:
        return (bar.symbol.upper(), bar.timeframe, bar.timestamp)

    def enqueue(self, bars: Iterable[LiveBar]) -> int:
        """Queue bars without doing database work on the MQTT callback thread.

        Repeated updates to the same candle replace the queued value, which
        naturally coalesces late prints received before the next flush.
        """
        accepted = 0
        dropped = 0
        with self._lock:
            for bar in bars:
                key = self._key(bar)
                self._pending.pop(key, None)
                self._pending[key] = bar
                accepted += 1
            while len(self._pending) > self._max_pending:
                self._pending.popitem(last=False)
                dropped += 1
        if dropped:
            logger.warning("live-bar persistence queue full; dropped %d oldest bars", dropped)
        return accepted

    def _take_pending(self) -> list[LiveBar]:
        with self._lock:
            if not self._pending:
                return []
            bars = list(self._pending.values())
            self._pending.clear()
            return bars

    def _restore_failed(self, bars: list[LiveBar]) -> None:
        """Requeue a failed batch while preserving newer queued revisions."""
        dropped = 0
        with self._lock:
            for bar in bars:
                key = self._key(bar)
                if key not in self._pending:
                    self._pending[key] = bar
            while len(self._pending) > self._max_pending:
                self._pending.popitem(last=False)
                dropped += 1
        if dropped:
            logger.warning(
                "live-bar persistence retry queue full; dropped %d oldest bars",
                dropped,
            )

    @staticmethod
    def _to_model(bar: LiveBar) -> Bar:
        return Bar(
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            timeframe=bar.timeframe,
            provider=bar.provider,
            data_status=DataStatus.HISTORICAL,
            source="raw",
        )

    def flush_once(self) -> int:
        """Persist one pending batch; failures remain queued for retry."""
        bars = self._take_pending()
        if not bars:
            return 0

        try:
            from backend.database import SessionLocal
            from backend.market_data.services.cache import _redis_cache
            from backend.repositories.bar_repository import upsert_stream_bars

            db = SessionLocal()
            try:
                written = upsert_stream_bars(db, [self._to_model(bar) for bar in bars])
            finally:
                db.close()

            # Any matching historical-series cache may predate this write.
            # Invalidation is safe even when an authoritative REST row made
            # the conditional stream upsert a no-op.
            for symbol in {bar.symbol.upper() for bar in bars}:
                _redis_cache.invalidate_bars_for_symbol(symbol)
            return written
        except Exception as exc:  # noqa: BLE001
            self._restore_failed(bars)
            logger.warning(
                "live-bar persistence failed (%d bars retained for retry): %s",
                len(bars),
                exc,
            )
            return 0

    def finalize_elapsed(self) -> int:
        """Close elapsed minute buckets and queue them for durable storage."""
        from backend.market_data.services.engine_seeder import engine_registry
        from backend.utils.timezone import now_ny

        completed = self._aggregator.finalize_elapsed(now_ny())
        for bar in completed:
            engine_registry.dispatch_bar(
                symbol=bar.symbol,
                timeframe=bar.timeframe,
                price=bar.close,
                volume=bar.volume,
                timestamp=bar.timestamp,
                high=bar.high,
                low=bar.low,
                open_price=bar.open,
            )
        return self.enqueue(completed)

    def _run(self) -> None:
        while not self._stop.wait(self._flush_interval):
            self.finalize_elapsed()
            self.flush_once()

    def start(self) -> None:
        """Start the idempotent background writer."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="live-bar-persistence",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop the writer and synchronously persist the final queued batch."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            # SQLite's configured busy timeout is 30 seconds. Give an
            # in-flight transaction enough time to finish, but never start a
            # competing final flush if the writer is still using the queue.
            thread.join(timeout=max(35.0, self._flush_interval * 2))
            if thread.is_alive():
                logger.warning("live-bar persistence writer did not stop cleanly")
                return
        self._thread = None
        self.finalize_elapsed()
        self.flush_once()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)


live_bar_persistence = LiveBarPersistence()
