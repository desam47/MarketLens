"""Tests for the provider-backed earnings-exclusion scanner constraint."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.scanner.router import _without_upcoming_earnings
from backend.scanner.scanner import ScanResult
from backend.utils.timezone import now_ny


def _result(symbol: str) -> ScanResult:
    return ScanResult(symbol, datetime.now(UTC))


@patch("backend.api.scanner.router.events_for_symbol")
def test_excludes_only_symbols_with_known_earnings_inside_the_window(mock_events):
    today = now_ny().date()
    mock_events.side_effect = lambda symbol: (
        [{"symbol": "AAPL", "event_type": "earnings", "date": (today + timedelta(days=3)).isoformat()}]
        if symbol == "AAPL" else []
    )

    remaining = _without_upcoming_earnings([_result("AAPL"), _result("MSFT")], 7)

    assert [result.symbol for result in remaining] == ["MSFT"]


@patch("backend.api.scanner.router.events_for_symbol")
@patch("backend.api.scanner.router.market_scanner")
def test_filter_endpoint_applies_earnings_exclusion_after_technical_filters(mock_scanner, mock_events):
    today = now_ny().date()
    mock_scanner.scan_results = {"AAPL": _result("AAPL"), "MSFT": _result("MSFT")}
    mock_events.side_effect = lambda symbol: (
        [{"symbol": "AAPL", "event_type": "earnings", "date": (today + timedelta(days=3)).isoformat()}]
        if symbol == "AAPL" else []
    )

    response = TestClient(app).post("/api/scanner/filter", json={
        "filters": [{"type": "exclude_earnings_within_days", "params": {"days": 7}}],
        "match": "AND",
    })

    assert response.status_code == 200
    assert [result["symbol"] for result in response.json()] == ["MSFT"]
