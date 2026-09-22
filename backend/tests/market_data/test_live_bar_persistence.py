from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from backend.market_data.streaming.live_bar_persistence import LiveBarPersistence
from backend.market_data.streaming.live_bars import LiveBar, LiveBarAggregator
from backend.models.market_data import DataStatus

ET = ZoneInfo("America/New_York")


def _bar(*, close: float = 101.0, volume: int = 25) -> LiveBar:
    return LiveBar(
        symbol="AAPL",
        timestamp=datetime(2026, 9, 21, 10, 0, tzinfo=ET),
        open=100.0,
        high=max(101.0, close),
        low=99.0,
        close=close,
        volume=volume,
    )


def test_flush_persists_historical_bar_and_invalidates_cache():
    writer = LiveBarPersistence()
    db = MagicMock()
    cache = MagicMock()
    writer.enqueue([_bar()])

    with (
        patch("backend.database.SessionLocal", return_value=db),
        patch("backend.repositories.bar_repository.upsert_stream_bars", return_value=1) as upsert,
        patch("backend.market_data.services.cache._redis_cache", cache),
    ):
        assert writer.flush_once() == 1

    persisted = upsert.call_args.args[1][0]
    assert persisted.provider == "webull_stream"
    assert persisted.data_status == DataStatus.HISTORICAL
    assert persisted.close == 101.0
    cache.invalidate_bars_for_symbol.assert_called_once_with("AAPL")
    db.close.assert_called_once()
    assert writer.pending_count == 0


def test_enqueue_coalesces_revisions_of_same_candle():
    writer = LiveBarPersistence()
    writer.enqueue([_bar(close=101.0, volume=25)])
    writer.enqueue([_bar(close=102.0, volume=40)])

    db = MagicMock()
    with (
        patch("backend.database.SessionLocal", return_value=db),
        patch("backend.repositories.bar_repository.upsert_stream_bars", return_value=1) as upsert,
        patch("backend.market_data.services.cache._redis_cache"),
    ):
        writer.flush_once()

    persisted = upsert.call_args.args[1]
    assert len(persisted) == 1
    assert persisted[0].close == 102.0
    assert persisted[0].volume == 40


def test_failed_flush_requeues_bar_for_retry():
    writer = LiveBarPersistence()
    writer.enqueue([_bar()])

    with (
        patch("backend.database.SessionLocal", side_effect=RuntimeError("database busy")),
        patch("backend.market_data.services.cache._redis_cache"),
    ):
        assert writer.flush_once() == 0

    assert writer.pending_count == 1


def test_timer_completion_dispatches_and_queues_inactive_symbol_bar():
    aggregator = LiveBarAggregator()
    aggregator.update("AAPL", 100.0, 10, datetime(2026, 9, 21, 10, 0, 5, tzinfo=ET))
    writer = LiveBarPersistence(aggregator=aggregator)

    with (
        patch(
            "backend.utils.timezone.now_ny",
            return_value=datetime(2026, 9, 21, 10, 1, 0, tzinfo=ET),
        ),
        patch(
            "backend.market_data.services.engine_seeder.engine_registry.dispatch_bar"
        ) as dispatch,
    ):
        assert writer.finalize_elapsed() == 1
        assert writer.finalize_elapsed() == 0

    assert writer.pending_count == 1
    dispatch.assert_called_once()
