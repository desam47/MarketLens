from datetime import datetime, timezone

from backend.market_data.streaming.live_quotes import LiveQuoteCache


def test_live_quote_cache_suppresses_exact_duplicate_events():
    cache = LiveQuoteCache()
    timestamp = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)

    first = cache.update("AAPL", price=200.0, volume=10, timestamp=timestamp, event_type="trade")
    duplicate = cache.update("AAPL", price=200.0, volume=10, timestamp=timestamp, event_type="trade")

    assert first is not None
    assert duplicate is None


def test_live_quote_cache_keeps_bbo_fields_when_trade_omits_them():
    cache = LiveQuoteCache()
    timestamp = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)
    cache.update("AAPL", price=200.0, bid=199.99, ask=200.01, timestamp=timestamp, event_type="bbo")

    updated = cache.update("AAPL", price=200.02, volume=50, timestamp=timestamp, event_type="trade")

    assert updated is not None
    assert updated["bid"] == 199.99
    assert updated["ask"] == 200.01
