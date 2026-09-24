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


def test_weakness_has_relative_fallback_when_no_name_is_down() -> None:
    strongest = _result("MSFT", 1.5)
    weakest = _result("AAPL", 0.8)
    strongest.scores = {"momentum": 8.0, "rsi": 4.0}
    weakest.scores = {"momentum": 1.0, "rsi": 1.0}

    briefing = build_watchlist_intelligence(
        [strongest, weakest], watchlist_size=2
    )

    assert briefing["top_bearish"] == []
    assert briefing["deteriorating"] == []
    assert briefing["weakest"][0]["symbol"] == "AAPL"
    assert briefing["weakest"][0]["metric"] == 1.0


def test_weakness_falls_back_to_multi_timeframe_signals_when_scores_are_missing() -> None:
    aapl = _result("AAPL", 0.5)
    msft = _result("MSFT", 0.7)
    aapl.scores = {}
    msft.scores = {}
    aapl.trend_signals = {
        "ONE_DAY": {"direction": "downtrend", "confidence": 0.9},
        "ONE_WEEK": {"direction": "downtrend", "confidence": 0.8},
    }
    msft.trend_signals = {
        "ONE_DAY": {"direction": "uptrend", "confidence": 0.9},
    }

    briefing = build_watchlist_intelligence([aapl, msft], watchlist_size=2)

    assert briefing["weakest"][0]["symbol"] == "AAPL"
    assert briefing["weakest"][0]["metric_label"] == "multi-timeframe direction score"
    assert briefing["weakest"][0]["metric"] == -2.0
    assert briefing["weakest"][0]["score"] is None


def test_relative_strength_can_be_scoped_to_an_explicit_benchmark() -> None:
    aapl = _result("AAPL", 0.5)
    msft = _result("MSFT", 0.7)
    aapl.indicator_values.update({"rs_pct_SPY": 4.0, "rs_pct_QQQ": -2.5})
    msft.indicator_values.update({"rs_pct_SPY": -1.0, "rs_pct_QQQ": 1.5})

    briefing = build_watchlist_intelligence(
        [aapl, msft], watchlist_size=2, benchmark_symbol="QQQ"
    )

    assert briefing["benchmark_symbol"] == "QQQ"
    assert briefing["relative_strength"][0]["symbol"] == "MSFT"
    assert briefing["relative_strength"][0]["details"]["benchmark"] == "QQQ"


def test_cold_or_partial_cache_is_reported_instead_of_triggering_a_scan() -> None:
    briefing = build_watchlist_intelligence([], watchlist_size=3)

    assert briefing["data_status"] == "warming"
    assert briefing["analyzed_symbols"] == 0
    assert any("0 of 3" in warning for warning in briefing["warnings"])
