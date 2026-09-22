"""
Wiring between the Webull MQTT stream and the rest of the app.

The stream feeds the tape engine and a bounded local 1-minute bar
aggregator. The aggregator updates the shared realtime chart channel
immediately, dispatches completed candles to in-memory analysis engines, and
queues completed candles for durable storage. REST remains the authoritative
historical/fallback source.

``on_stream_snapshot`` / ``on_stream_trade`` are the WebullStreamClient
callbacks (set in main.py's lifespan). They run on the SDK's thread.

  trade    -> engine_registry.dispatch_trade  (Time & Sales)
           -> local 1m OHLCV aggregation -> realtime bar broadcast
              and completed-bar dispatch
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
    if payload is None:
        return
    from backend.services.tick_replay import tick_replay_store
    tick_replay_store.record(symbol, payload)
    try:
        from backend.api.realtime.ws_router import publish_live_quote
        publish_live_quote(symbol, payload)
    except Exception as e:  # noqa: BLE001
        logger.debug("stream snapshot -> quote broadcast failed for %s: %s", symbol, e)
    try:
        from backend.api.tape.registry import get_tape_engine

        get_tape_engine(symbol, seed=False).note_price(price, ts)
    except Exception as e:  # noqa: BLE001
        logger.debug("stream snapshot -> tape failed for %s: %s", symbol, e)
    engine_registry.dispatch_microstructure(symbol, payload)


def on_stream_trade(symbol, price, size, ts, side) -> None:
    from backend.market_data.streaming.live_quotes import live_quote_cache

    payload = live_quote_cache.update(symbol, price=price, volume=size, timestamp=ts, event_type="trade")
    if payload is None:
        return
    from backend.services.tick_replay import tick_replay_store
    tick_replay_store.record(symbol, payload)
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
    try:
        from backend.market_data.streaming.live_bars import live_bar_aggregator

        update = live_bar_aggregator.update(symbol, price, size, _ensure_aware(ts))
        if update is not None:
            # Completed bars advance the shared in-memory analysis engines at
            # the same cadence as REST-ingested 1m bars. The current forming
            # candle is sent directly to chart subscribers below.
            for bar in update.completed:
                engine_registry.dispatch_bar(
                    symbol=bar.symbol,
                    timeframe=bar.timeframe,
                    price=bar.close,
                    volume=bar.volume,
                    timestamp=bar.timestamp,
                    high=bar.high,
                    low=bar.low,
                    open_price=bar.open,
                    data_status=getattr(bar, "data_status", None),
                    session=getattr(bar, "session", None),
                )
            from backend.api.realtime.ws_router import publish_live_bar

            publish_live_bar(symbol, update.current.as_payload(), "1m")
            durable_bars = (*update.completed, *update.revised_closed)
            if durable_bars:
                from backend.market_data.streaming.live_bar_persistence import (
                    live_bar_persistence,
                )

                live_bar_persistence.enqueue(durable_bars)
    except Exception as e:  # noqa: BLE001
        # Aggregation is an enhancement over the REST fallback and must never
        # interrupt the tape or quote path when a malformed trade slips
        # through or a browser broadcast is unavailable.
        logger.debug("stream trade -> local bar aggregation failed for %s: %s", symbol, e)
    engine_registry.dispatch_microstructure(symbol, payload)


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
    if payload is None:
        return
    from backend.services.tick_replay import tick_replay_store
    tick_replay_store.record(symbol, payload)
    try:
        from backend.api.realtime.ws_router import publish_live_quote
        publish_live_quote(symbol, payload)
    except Exception as e:  # noqa: BLE001
        logger.debug("stream BBO -> quote broadcast failed for %s: %s", symbol, e)
    engine_registry.dispatch_microstructure(symbol, payload)
