"""
Engine seeding helpers + live-tick registry.

The analysis engines (regime, trend, confluence) are in-memory state machines
that build up signals from a stream of price/volume updates. On a fresh
process (or after a restart) they start empty, so the API would return
"unknown" until enough new ticks arrive.

This module:
  * Seeds engines from the persisted QuoteModel/BarModel rows on first use
    so the API returns real signals immediately after restart.
  * Hosts a process-wide EngineRegistry that routers register their engines
    into and the ingestion service pushes fresh ticks through, so the
    in-memory engines stay current with live data — not just what was
    replayed at startup.
"""
import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime

from backend.database import SessionLocal
from backend.models.market_data_sql import BarModel, QuoteModel
from backend.utils.timezone import ensure_aware_ny

logger = logging.getLogger(__name__)


def _ensure_aware(dt) -> datetime | None:
    """Normalize a datetime to timezone-aware America/New_York.

    Thin alias for ``backend.utils.timezone.ensure_aware_ny`` — kept as a
    module-level name because the ingestion service imports it from here.
    Naive datetimes are NY local time (project convention); see that module
    for why interpreting them as UTC is a 4-5h bug.
    """
    return ensure_aware_ny(dt)


def seed_engine_from_quotes(symbol: str, update_fn: Callable, max_points: int = 200) -> int:
    """
    Replay a symbol's stored quotes through an engine's update() method.

    Args:
        symbol: Ticker symbol, e.g. "AAPL".
        update_fn: Callable matching `def update(price, volume, timestamp, **kw)`.
        max_points: Cap on quotes replayed (newest first).

    Returns:
        Number of quotes replayed.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(QuoteModel)
            .filter(QuoteModel.symbol == symbol.upper())
            .order_by(QuoteModel.timestamp.desc())
            .limit(max_points)
            .all()
        )
        rows.reverse()  # feed oldest → newest
        for q in rows:
            try:
                update_fn(price=float(q.price or 0.0),
                          volume=int(q.volume or 0),
                          timestamp=_ensure_aware(q.timestamp))
            except Exception as e:
                logger.debug(f"Seed tick failed for {symbol} @ {q.timestamp}: {e}")
        return len(rows)
    finally:
        db.close()


def seed_engine_from_bars(symbol: str, timeframe: str, update_fn: Callable,
                          max_points: int = 200) -> int:
    """Replay a symbol+timeframe's stored bars through an engine update()."""
    db = SessionLocal()
    try:
        rows = (
            db.query(BarModel)
            .filter(BarModel.symbol == symbol.upper(), BarModel.timeframe == timeframe)
            .order_by(BarModel.timestamp.desc())
            .limit(max_points)
            .all()
        )
        rows.reverse()
        for b in rows:
            try:
                update_fn(price=float(b.close or 0.0),
                          volume=int(b.volume or 0),
                          timestamp=_ensure_aware(b.timestamp))
            except Exception as e:
                logger.debug(f"Seed bar failed for {symbol}/{timeframe} @ {b.timestamp}: {e}")
        return len(rows)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Live-tick engine registry
# ---------------------------------------------------------------------------
# Engine updates are short synchronous calls. The ingestion service runs in
# the server's asyncio loop, so a direct call is fine — no need to schedule
# on the loop. We still guard with a lock so a router that registers while
# a tick is being dispatched doesn't trip an iteration-during-mutation bug.

class EngineRegistry:
    """Process-wide registry of analysis-engine update callbacks.

    Routers register `(symbol, engine_kind, update_fn)` when they create an
    engine. The ingestion service calls `dispatch_quote()` / `dispatch_bar()`
    on every fresh tick, which fans out to every registered engine for that
    symbol. Engines that don't care about a particular event kind (e.g. the
    multitimeframe engine which only wants bars) simply won't be registered
    for it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (kind, symbol) -> list of update callables
        # kind is "quote" or "bar:{timeframe}" e.g. "bar:1m"
        self._entries: dict[str, list[Callable]] = defaultdict(list)

    @staticmethod
    def _key(kind: str, symbol: str) -> str:
        return f"{kind.upper()}::{symbol.upper()}"

    def register(self, kind: str, symbol: str, update_fn: Callable) -> None:
        """Register an engine's update callback for a given event kind + symbol."""
        key = self._key(kind, symbol)
        with self._lock:
            if update_fn not in self._entries[key]:
                self._entries[key].append(update_fn)
                logger.debug(f"Registered {kind} engine for {symbol} (total={len(self._entries[key])})")

    def unregister(self, kind: str, symbol: str, update_fn: Callable) -> bool:
        """Remove a previously-registered callback.

        Returns True if the callback was found and removed, False otherwise.
        Idempotent — safe to call when the callback isn't registered.
        """
        key = self._key(kind, symbol)
        with self._lock:
            entries = self._entries.get(key)
            if not entries:
                return False
            try:
                entries.remove(update_fn)
            except ValueError:
                return False
            if not entries:
                del self._entries[key]
            return True

    def dispatch_quote(self, symbol: str, price: float, volume: float,
                       timestamp, high=None, low=None, open_price=None) -> int:
        """Fan out a fresh quote to every engine registered for QUOTE on this symbol.

        Returns the number of engines notified. The regime engine wants OHLCV
        data and is registered as QUOTE; trend + multitimeframe are registered
        as bar:{timeframe} so they only see bar events.
        """
        key = self._key("quote", symbol)
        with self._lock:
            callbacks = list(self._entries.get(key, []))
        if not callbacks:
            return 0
        notified = 0
        for cb in callbacks:
            try:
                cb(price=price, volume=volume, timestamp=timestamp,
                   high=high, low=low, open_price=open_price)
                notified += 1
            except Exception as e:
                # An engine bug must not break the ingestion loop
                logger.warning(f"Engine update failed for {symbol} (quote): {e}")
        return notified

    def dispatch_bar(self, symbol: str, timeframe: str, price: float,
                     volume: float, timestamp,
                     high: float | None = None,
                     low: float | None = None,
                     open_price: float | None = None) -> int:
        """Fan out a fresh bar to engines registered for that timeframe on this symbol.

        Callbacks are invoked with the full bar context: ``symbol``, ``timeframe``,
        ``price``, ``volume``, ``timestamp`` plus optional OHLCV. Engine
        ``update()`` methods should accept these as ``**kwargs`` (or
        explicitly named params) to remain forward-compatible.
        """
        key = self._key(f"bar:{timeframe}", symbol)
        with self._lock:
            callbacks = list(self._entries.get(key, []))
        logger.debug(
            f"dispatch_bar: {symbol}/{timeframe} @ {timestamp} — "
            f"{len(callbacks)} engine(s) registered for key={key!r}"
        )
        if not callbacks:
            return 0
        notified = 0
        for cb in callbacks:
            try:
                cb(symbol=symbol, timeframe=timeframe,
                   price=price, volume=volume, timestamp=timestamp,
                   high=high, low=low, open_price=open_price)
                notified += 1
                logger.debug(f"dispatch_bar: notified engine cb={cb} for {symbol}/{timeframe}")
            except Exception as e:
                logger.warning(f"Engine update failed for {symbol}/{timeframe} (bar): {e}")
        return notified


# Single process-wide instance. Import this in routers (to register) and
# in the ingestion service (to dispatch).
engine_registry = EngineRegistry()
