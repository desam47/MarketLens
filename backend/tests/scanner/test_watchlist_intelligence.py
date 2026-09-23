"""Focused tests for the provider-free Watchlist Intelligence aggregation."""

from datetime import datetime

from backend.models.market_data import DataStatus, Quote
from backend.scanner.scanner import ScanResult
from backend.scanner.watchlist_intelligence import build_watchlist_intelligence


def _result(symbol: str, change_pct: float) -> ScanResult:
    result = ScanResult(symbol, datetime(2026, 9, 23, 10, 0))
    result.quote = Quote(
        symbol=symbol,
        price=100,
        timestamp=datetime(2026, 9, 23, 10, 0),
        provider="test",
        data_status=DataStatus.LIVE,
    )
    result.change_pct = change_pct
    result.scores = {"momentum": change_pct, "trend_strength": 2}
    return result


def test_briefing_uses_selected_session_snapshot_for_price_rankings() -> None:
    aapl = _result("AAPL", 1.0)
    nvda = _result("NVDA", -1.0)
    aapl.indicator_values.update({"breakout_20": True, "breakout_pct_20": 2.2, "volume_ratio": 2.0, "rs_pct_SPY": 3.5})
    aapl.trend_signals = {
        "ONE_MINUTE": {"direction": "uptrend", "confidence": 0.8},
        "FIVE_MINUTE": {"direction": "uptrend", "confidence": 0.7},
        "ONE_DAY": {"direction": "uptrend", "confidence": 0.6},
    }
    nvda.indicator_values["rs_pct_SPY"] = -2.0

    briefing = build_watchlist_intelligence(
        [aapl, nvda],
        watchlist_size=2,
        session_scope="premarket",
        session_snapshots={
            "AAPL": {"price": 102, "change_pct": 2.0, "timestamp": "2026-09-23T08:00:00-04:00", "session": "premarket"},
            "NVDA": {"price": 98, "change_pct": -4.0, "timestamp": "2026-09-23T08:00:00-04:00", "session": "premarket"},
        },
    )

    assert briefing["data_status"] == "ready"
    assert briefing["top_bullish"][0]["symbol"] == "AAPL"
    assert briefing["top_bullish"][0]["change_pct"] == 2.0
    assert briefing["top_bearish"][0]["symbol"] == "NVDA"
    assert briefing["top_bearish"][0]["change_pct"] == -4.0
    assert [entry["symbol"] for entry in briefing["breakouts"]] == ["AAPL"]
    assert [entry["symbol"] for entry in briefing["volume_spikes"]] == ["AAPL"]
    assert briefing["relative_strength"][0]["details"]["benchmark"] == "SPY"
    assert briefing["mtf_alignment"][0]["details"]["direction"] == "bullish"
    assert briefing["sector_rotation"][0]["sector"] == "Technology"


def test_no_session_never_silently_falls_back_to_all_session_movers() -> None:
    briefing = build_watchlist_intelligence(
        [_result("AAPL", 4.0)], watchlist_size=1, session_scope="none"
    )

    assert briefing["top_bullish"] == []
    assert briefing["top_bearish"] == []
    assert any("No market session is selected" in warning for warning in briefing["warnings"])


def test_cold_or_partial_cache_is_reported_instead_of_triggering_a_scan() -> None:
    briefing = build_watchlist_intelligence([], watchlist_size=3)

    assert briefing["data_status"] == "warming"
    assert briefing["analyzed_symbols"] == 0
    assert any("0 of 3" in warning for warning in briefing["warnings"])
