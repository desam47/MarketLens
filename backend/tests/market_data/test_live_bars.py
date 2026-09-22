from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from backend.market_data.services.engine_seeder import engine_registry
from backend.market_data.streaming.bridge import on_stream_trade
from backend.market_data.streaming.live_bars import LiveBarAggregator

ET = ZoneInfo("America/New_York")


def test_aggregates_ohlcv_within_one_minute():
    aggregator = LiveBarAggregator()
    first = aggregator.update("aapl", 100.0, 10, datetime(2026, 9, 21, 10, 0, 5, tzinfo=ET))
    second = aggregator.update("AAPL", 101.5, 25, datetime(2026, 9, 21, 10, 0, 45, tzinfo=ET))

    assert first is not None
    assert second is not None
    assert second.current.symbol == "AAPL"
    assert second.current.timestamp == datetime(2026, 9, 21, 10, 0, tzinfo=ET)
    assert second.current.open == 100.0
    assert second.current.high == 101.5
    assert second.current.low == 100.0
    assert second.current.close == 101.5
    assert second.current.volume == 35
    assert second.completed == ()


def test_rollover_emits_previous_bar_once_and_accepts_late_print():
    aggregator = LiveBarAggregator()
    aggregator.update("AAPL", 100.0, 10, datetime(2026, 9, 21, 10, 0, 5, tzinfo=ET))
    rollover = aggregator.update("AAPL", 101.0, 20, datetime(2026, 9, 21, 10, 1, 2, tzinfo=ET))

    assert rollover is not None
    assert len(rollover.completed) == 1
    assert rollover.completed[0].timestamp == datetime(2026, 9, 21, 10, 0, tzinfo=ET)
    assert rollover.completed[0].close == 100.0

    late = aggregator.update("AAPL", 99.5, 5, datetime(2026, 9, 21, 10, 0, 55, tzinfo=ET))
    assert late is not None
    assert late.current.timestamp == datetime(2026, 9, 21, 10, 0, tzinfo=ET)
    assert late.current.low == 99.5
    assert late.completed == ()
    assert late.revised_closed == (late.current,)

    next_rollover = aggregator.update(
        "AAPL", 102.0, 10, datetime(2026, 9, 21, 10, 2, 1, tzinfo=ET)
    )
    assert next_rollover is not None
    assert [bar.timestamp for bar in next_rollover.completed] == [
        datetime(2026, 9, 21, 10, 1, tzinfo=ET)
    ]


def test_duplicate_trade_does_not_inflate_volume():
    aggregator = LiveBarAggregator()
    timestamp = datetime(2026, 9, 21, 10, 0, 5, tzinfo=ET)
    assert aggregator.update("AAPL", 100.0, 10, timestamp) is not None
    assert aggregator.update("AAPL", 100.0, 10, timestamp) is None


def test_timer_finalizes_elapsed_bar_once_and_late_print_revises_it():
    aggregator = LiveBarAggregator()
    aggregator.update("AAPL", 100.0, 10, datetime(2026, 9, 21, 10, 0, 5, tzinfo=ET))

    assert aggregator.finalize_elapsed(datetime(2026, 9, 21, 10, 0, 59, tzinfo=ET)) == ()
    completed = aggregator.finalize_elapsed(datetime(2026, 9, 21, 10, 1, 0, tzinfo=ET))
    assert len(completed) == 1
    assert completed[0].timestamp == datetime(2026, 9, 21, 10, 0, tzinfo=ET)
    assert aggregator.finalize_elapsed(datetime(2026, 9, 21, 10, 1, 10, tzinfo=ET)) == ()

    late = aggregator.update("AAPL", 99.0, 5, datetime(2026, 9, 21, 10, 0, 55, tzinfo=ET))
    assert late is not None
    assert late.completed == ()
    assert late.revised_closed == (late.current,)
    assert late.current.volume == 15


def test_old_late_trade_is_ignored():
    aggregator = LiveBarAggregator()
    aggregator.update("AAPL", 100.0, 10, datetime(2026, 9, 21, 10, 5, tzinfo=ET))

    assert aggregator.update(
        "AAPL", 99.0, 10, datetime(2026, 9, 21, 10, 2, 30, tzinfo=ET)
    ) is None


def test_stream_bridge_publishes_the_shared_current_bar():
    from backend.market_data.streaming.live_bars import live_bar_aggregator

    live_bar_aggregator.reset()
    timestamp = datetime(2026, 9, 21, 10, 0, 5, tzinfo=ET)
    payload = {
        "price": 100.0,
        "volume": 10,
        "timestamp": timestamp,
        "event_type": "trade",
    }
    try:
        with (
            patch("backend.market_data.streaming.live_quotes.live_quote_cache.update", return_value=payload),
            patch("backend.services.tick_replay.tick_replay_store.record"),
            patch("backend.api.realtime.ws_router.publish_live_quote"),
            patch("backend.api.realtime.ws_router.publish_live_bar") as publish_bar,
            patch.object(engine_registry, "dispatch_trade"),
            patch.object(engine_registry, "dispatch_microstructure"),
        ):
            on_stream_trade("AAPL", 100.0, 10, timestamp, "buy")

        publish_bar.assert_called_once()
        symbol, bar_payload, timeframe = publish_bar.call_args.args
        assert symbol == "AAPL"
        assert timeframe == "1m"
        assert bar_payload["open"] == 100.0
        assert bar_payload["volume"] == 10
    finally:
        live_bar_aggregator.reset()


def test_stream_bridge_queues_completed_and_late_revised_bars():
    from backend.market_data.streaming.live_bars import live_bar_aggregator

    live_bar_aggregator.reset()
    payload = {"price": 100.0, "volume": 10, "event_type": "trade"}
    first_ts = datetime(2026, 9, 21, 10, 0, 5, tzinfo=ET)
    next_ts = datetime(2026, 9, 21, 10, 1, 2, tzinfo=ET)
    late_ts = datetime(2026, 9, 21, 10, 0, 55, tzinfo=ET)
    try:
        with (
            patch(
                "backend.market_data.streaming.live_quotes.live_quote_cache.update",
                return_value=payload,
            ),
            patch("backend.services.tick_replay.tick_replay_store.record"),
            patch("backend.api.realtime.ws_router.publish_live_quote"),
            patch("backend.api.realtime.ws_router.publish_live_bar"),
            patch(
                "backend.market_data.streaming.live_bar_persistence.live_bar_persistence.enqueue"
            ) as enqueue,
            patch.object(engine_registry, "dispatch_trade"),
            patch.object(engine_registry, "dispatch_bar") as dispatch_bar,
            patch.object(engine_registry, "dispatch_microstructure"),
        ):
            on_stream_trade("AAPL", 100.0, 10, first_ts, "buy")
            on_stream_trade("AAPL", 101.0, 20, next_ts, "buy")
            on_stream_trade("AAPL", 99.5, 5, late_ts, "sell")

        assert enqueue.call_count == 2
        completed = enqueue.call_args_list[0].args[0]
        revised = enqueue.call_args_list[1].args[0]
        assert len(completed) == 1
        assert completed[0].close == 100.0
        assert len(revised) == 1
        assert revised[0].low == 99.5
        assert revised[0].volume == 15
        dispatch_bar.assert_called_once()
    finally:
        live_bar_aggregator.reset()
