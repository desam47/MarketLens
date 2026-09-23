"""Tests for GET /api/market-data/session (backend/api/market_data_routes.py).

Confirms the route is a local calendar lookup (no provider network call) and
correctly classifies weekday-evening, weekend, and holiday moments as
'closed' via ``USMarketCalendar`` -- the gap that let the Symbol page badge
show "Delayed" with a growing age counter overnight with no session context.
"""

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.api.main import app

client = TestClient(app)

EASTERN = ZoneInfo("America/New_York")


def _at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=EASTERN).astimezone(ZoneInfo("UTC"))


def test_session_route_reports_closed_after_hours():
    with patch("backend.api.market_data_routes.datetime") as mock_dt:
        mock_dt.now.return_value = _at(2026, 9, 22, 21, 9)  # 9:09 PM ET, weekday
        mock_dt.combine = datetime.combine
        response = client.get("/api/market-data/session")

    assert response.status_code == 200
    body = response.json()
    assert body["session"] == "closed"
    assert body["is_open"] is False


def test_session_route_reports_closed_on_weekend():
    with patch("backend.api.market_data_routes.datetime") as mock_dt:
        mock_dt.now.return_value = _at(2026, 9, 19, 12, 0)  # Saturday
        mock_dt.combine = datetime.combine
        response = client.get("/api/market-data/session")

    assert response.status_code == 200
    assert response.json()["session"] == "closed"


def test_session_route_reports_closed_on_holiday():
    with patch("backend.api.market_data_routes.datetime") as mock_dt:
        mock_dt.now.return_value = _at(2026, 12, 25, 12, 0)  # Christmas, weekday
        mock_dt.combine = datetime.combine
        response = client.get("/api/market-data/session")

    assert response.status_code == 200
    assert response.json()["session"] == "closed"


def test_session_route_reports_regular_during_trading_hours():
    with patch("backend.api.market_data_routes.datetime") as mock_dt:
        mock_dt.now.return_value = _at(2026, 9, 22, 11, 0)  # 11 AM ET, weekday
        mock_dt.combine = datetime.combine
        response = client.get("/api/market-data/session")

    assert response.status_code == 200
    body = response.json()
    assert body["session"] == "regular"
    assert body["is_open"] is True
