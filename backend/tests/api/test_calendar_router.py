"""Tests for provider-backed earnings and corporate-event calendar routes."""

from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api.main import app


class TestSymbolCalendarRoute:
    def setup_method(self):
        self.client = TestClient(app)

    @patch("backend.api.calendar.router.events_for_symbol")
    def test_returns_normalized_events_for_a_symbol(self, mock_events):
        mock_events.return_value = [{"symbol": "AAPL", "event_type": "earnings", "date": date(2026, 9, 25).isoformat()}]

        response = self.client.get("/api/calendar/symbol/aapl")

        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "AAPL"
        assert data["provider"] == "yfinance"
        assert data["events"] == [{
            "symbol": "AAPL", "event_type": "earnings", "date": "2026-09-25", "source": "yfinance",
        }]
        mock_events.assert_called_once_with("AAPL")
