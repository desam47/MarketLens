from backend.services.tick_replay import TickReplayStore


def test_tick_replay_keeps_events_in_order_and_bounds_size():
    store = TickReplayStore(max_events_per_symbol=2, retention_seconds=60)
    store.record("aapl", {"price": 100, "timestamp": "one", "received_at": 1, "event_type": "trade"})
    store.record("AAPL", {"price": 101, "timestamp": "two", "received_at": 2, "event_type": "bbo"})
    store.record("AAPL", {"price": 102, "timestamp": "three", "received_at": 3, "event_type": "trade"})

    events = store.get("AAPL")
    assert [event["price"] for event in events] == [101, 102]
    assert events[0]["symbol"] == "AAPL"
