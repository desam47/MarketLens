from backend.services.tick_replay import TickReplayStore, reconstruct_tick_signals


def test_tick_replay_keeps_events_in_order_and_bounds_size():
    store = TickReplayStore(max_events_per_symbol=2, retention_seconds=60)
    store.record("aapl", {"price": 100, "timestamp": "one", "received_at": 1, "event_type": "trade"})
    store.record("AAPL", {"price": 101, "timestamp": "two", "received_at": 2, "event_type": "bbo"})
    store.record("AAPL", {"price": 102, "timestamp": "three", "received_at": 3, "event_type": "trade"})

    events = store.get("AAPL")
    assert [event["price"] for event in events] == [101, 102]
    assert events[0]["symbol"] == "AAPL"


def test_reconstruct_tick_signals_builds_causal_one_minute_candles():
    events = [
        {
            "price": price,
            "volume": 10,
            "timestamp": timestamp,
            "event_type": "trade",
        }
        for price, timestamp in (
            (100.0, "2026-09-21T10:00:05-04:00"),
            (100.5, "2026-09-21T10:00:35-04:00"),
            (101.0, "2026-09-21T10:01:05-04:00"),
        )
    ]

    candles = reconstruct_tick_signals("aapl", events)

    assert len(candles) == 2
    assert candles[0]["open"] == 100.0
    assert candles[0]["high"] == 100.5
    assert candles[0]["close"] == 100.5
    assert candles[0]["volume"] == 20
    assert candles[0]["tick_count"] == 2
    assert candles[1]["close"] == 101.0
    assert all("signal_state" in candle for candle in candles)
