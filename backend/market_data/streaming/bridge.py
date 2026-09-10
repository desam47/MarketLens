"""
Wiring between the Webull MQTT stream and the rest of the app.

``on_stream_snapshot`` / ``on_stream_trade`` are set as the
``WebullStreamClient`` callbacks in ``main.py``'s lifespan. They run on
the SDK's background thread.

  snapshot -> engine_registry.dispatch_quote  (+ Redis quote, throttled DB row)
  trade    -> engine_registry.dispatch_trade  (in-memory only; tape engine consumes it)
"""
from __future__ import annotations

import logging
import threading
import time

from backend.market_data.services.engine_seeder import _ensure_aware, engine_registry

logger = logging.getLogger(__name__)

# Per-symbol throttle for QuoteModel inserts — the stream can emit many
# snapshots/sec; the polled loop wrote one per 30s. 5s keeps continuity
# without row spam.
_DB_WRITE_INTERVAL = 5.0
_last_db_write: dict[str, float] = {}
_db_lock = threading.Lock()


def on_stream_snapshot(symbol, price, volume, ts, high, low, open_) -> None:
    aware = _ensure_aware(ts)
    engine_registry.dispatch_quote(
        symbol, price, volume or 0, timestamp=aware,
        high=high, low=low, open_price=open_,
    )
    _cache_and_persist_quote(symbol, price, volume, ts)


def on_stream_trade(symbol, price, size, ts, side) -> None:
    engine_registry.dispatch_trade(
        symbol, price, size or 0, timestamp=_ensure_aware(ts), side=side,
    )


def _cache_and_persist_quote(symbol, price, volume, ts) -> None:
    """Redis every time (cheap, keeps get_quote fresh); DB row throttled."""
    try:
        from backend.models.market_data import DataStatus, Quote
        from backend.market_data.services._providers import get_redis_cache

        quote = Quote(
            symbol=symbol, price=price, timestamp=ts,
            provider="webull_stream", data_status=DataStatus.LIVE,
            volume=int(volume) if volume else None,
        )
        get_redis_cache().set_quote(symbol, quote)
    except Exception as e:  # noqa: BLE001
        logger.debug("stream quote cache failed for %s: %s", symbol, e)
        return

    now = time.monotonic()
    with _db_lock:
        if now - _last_db_write.get(symbol, 0.0) < _DB_WRITE_INTERVAL:
            return
        _last_db_write[symbol] = now

    try:
        from backend.database import SessionLocal
        from backend.models.market_data_sql import QuoteModel

        db = SessionLocal()
        try:
            db.add(QuoteModel(
                symbol=symbol, price=price,
                volume=int(volume) if volume else None,
                timestamp=ts, provider="webull_stream", data_status="LIVE",
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("stream quote persist failed for %s: %s", symbol, e)
