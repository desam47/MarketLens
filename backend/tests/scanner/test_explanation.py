"""Tests for the scanner's human-readable explanation payload."""

from datetime import datetime, timedelta

from backend.models.market_data import DataStatus, Quote
from backend.scanner.explanation import build_signal_explanation
from backend.scanner.scanner import ScanResult


def _result(symbol: str = "AAPL", timestamp: datetime | None = None) -> ScanResult:
    result = ScanResult(symbol, timestamp or datetime.now())
    result.quote = Quote(
        symbol=symbol,
        price=150.0,
        timestamp=datetime.now() - timedelta(seconds=20),
        provider="test-provider",
        data_status=DataStatus.DELAYED,
        volume=2_000_000,
    )
    for key, value in {
        "price": 150.0,
        "rsi": 28.0,
        "macd": 1.2,
        "adx": 42.0,
        "volume_ratio": 2.1,
        "relative_strength": 1.8,
    }.items():
        result.add_indicator(key, value)
    result.add_trend_signal("1h", {"direction": "uptrend", "confidence": 0.8})
    result.add_trend_signal("1d", {"direction": "uptrend", "confidence": 0.7})
    result.add_score("momentum", 70.0)
    result.add_score("rsi", 35.0)
    result.signals = ["RSI_OVERSOLD_REVERSAL", "VOLUME_SPIKE"]
    return result


def test_explanation_contains_drivers_agreement_and_freshness():
    result = _result()

    explanation = build_signal_explanation(result)

    assert explanation["direction"] == "bullish"
    assert 0 <= explanation["confidence"] <= 100
    assert any(driver["key"] == "rsi" for driver in explanation["drivers"])
    assert any(driver["key"] == "signal:VOLUME_SPIKE" for driver in explanation["drivers"])
    assert explanation["timeframe_agreement"]["bullish"] == 2
    assert explanation["timeframe_agreement"]["alignment_pct"] == 100.0
    assert explanation["data_freshness"]["status"] in {"fresh", "recent"}
    assert explanation["data_freshness"]["provider"] == "test-provider"
    assert explanation["historical_performance"] is None


def test_explanation_reports_changes_from_previous_scan():
    previous = _result(timestamp=datetime.now() - timedelta(minutes=1))
    previous.signals = ["RSI_OVERSOLD"]
    previous.add_score("momentum", 20.0)
    current = _result()

    changes = build_signal_explanation(current, previous)["changes"]

    assert changes is not None
    assert "RSI_OVERSOLD_REVERSAL" in changes["signals_added"]
    assert "RSI_OVERSOLD" in changes["signals_removed"]
    assert changes["score_delta"] > 0
    assert changes["changed"] is True
