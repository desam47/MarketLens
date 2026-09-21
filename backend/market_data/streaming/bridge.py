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


def on_stream_snapshot(symbol, price, volume, ts, high, low, open_, bid=None, ask=None, bid_size=None, ask_size=None) -> None:
    from backend.market_data.streaming.live_quotes import live_quote_cache

    payload = live_quote_cache.update(
        symbol, price=price, volume=volume, timestamp=ts, bid=bid, ask=ask,
        bid_size=bid_size, ask_size=ask_size,
    )
    try:
        from backend.api.realtime.ws_router import publish_live_quote
        publish_live_quote(symbol, payload)
    except Exception as e:  # noqa: BLE001
        logger.debug("stream snapshot -> quote broadcast failed for %s: %s", symbol, e)
    try:
        from backend.api.tape.registry import get_tape_engine

        get_tape_engine(symbol).note_price(price, ts)
    except Exception as e:  # noqa: BLE001
        logger.debug("stream snapshot -> tape failed for %s: %s", symbol, e)


def on_stream_trade(symbol, price, size, ts, side) -> None:
    from backend.market_data.streaming.live_quotes import live_quote_cache

    payload = live_quote_cache.update(symbol, price=price, volume=size, timestamp=ts, event_type="trade")
    try:
        from backend.api.realtime.ws_router import publish_live_quote
        publish_live_quote(symbol, payload)
    except Exception as e:  # noqa: BLE001
        logger.debug("stream trade -> quote broadcast failed for %s: %s", symbol, e)
    try:
        engine_registry.dispatch_trade(
            symbol,
            price,
            size or 0,
            timestamp=_ensure_aware(ts),
            side=side,
        )
    except Exception as e:  # noqa: BLE001
        # Mirrors on_stream_snapshot's guard: without this, a malformed
        # trade tick's exception is only caught by _on_message's generic,
        # symbol-less handler in webull_stream.py, making it harder to
        # tell which symbol's tape feed broke from the logs alone.
        logger.debug("stream trade -> tape failed for %s: %s", symbol, e)


def on_stream_bbo(symbol, bid, ask, bid_size, ask_size, ts) -> None:
    from backend.market_data.streaming.live_quotes import live_quote_cache

    previous = live_quote_cache.get(symbol) or {}
    price = previous.get("price")
    if price is None and bid is not None and ask is not None:
        price = (bid + ask) / 2
    if price is None:
        return
    payload = live_quote_cache.update(
        symbol, price=price, timestamp=ts, bid=bid, ask=ask,
        bid_size=bid_size, ask_size=ask_size, event_type="bbo",
    )
    try:
        from backend.api.realtime.ws_router import publish_live_quote
        publish_live_quote(symbol, payload)
    except Exception as e:  # noqa: BLE001
        logger.debug("stream BBO -> quote broadcast failed for %s: %s", symbol, e)
