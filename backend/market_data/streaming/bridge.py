"""
Wiring between the Webull MQTT stream and the rest of the app.

Scoped MQTT (2026-09-10): the stream feeds ONLY the tape engine, which
is consumed by the AI features (build_context's `tape` section -> chat /
analysis / digest). It does NOT feed the trend / regime / scanner
engines or write quote/bar rows — those stay 100% REST-fed, so there's
no second source of truth for anything load-bearing and a stream outage
only empties the `tape` section.

``on_stream_snapshot`` / ``on_stream_trade`` are the WebullStreamClient
callbacks (set in main.py's lifespan). They run on the SDK's thread.

  trade    -> engine_registry.dispatch_trade  (kind "trade"; tape is the only consumer)
  snapshot -> TapeEngine.note_price           (keeps last_price fresh between prints)
"""
from __future__ import annotations

import logging

from backend.market_data.services.engine_seeder import _ensure_aware, engine_registry

logger = logging.getLogger(__name__)


def on_stream_snapshot(symbol, price, volume, ts, high, low, open_) -> None:
    try:
        from backend.api.tape.registry import get_tape_engine

        get_tape_engine(symbol).note_price(price, ts)
    except Exception as e:  # noqa: BLE001
        logger.debug("stream snapshot -> tape failed for %s: %s", symbol, e)


def on_stream_trade(symbol, price, size, ts, side) -> None:
    engine_registry.dispatch_trade(
        symbol, price, size or 0, timestamp=_ensure_aware(ts), side=side,
    )
