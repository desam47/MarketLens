"""Tests for the drawing-tools API router."""
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.models import DrawingTool
from backend.database import Base, engine


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_drawings():
    """Wipe the drawing_tools table before/after each test."""
    from backend.database import SessionLocal
    db = SessionLocal()
    try:
        db.query(DrawingTool).delete()
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(DrawingTool).delete()
        db.commit()
    finally:
        db.close()


def test_list_drawings_empty(client):
    resp = client.get("/api/drawing-tools")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_drawing(client):
    resp = client.post(
        "/api/drawing-tools",
        json={
            "symbol": "SPY",
            "timeframe": "1d",
            "drawing_type": "trend_line",
            "label": "Support",
            "color": "#10b981",
            "line_width": 2.0,
            "start_timestamp": "2026-08-01T00:00:00",
            "start_price": 540.50,
            "end_timestamp": "2026-08-30T00:00:00",
            "end_price": 580.00,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["symbol"] == "SPY"
    assert data["drawing_type"] == "trend_line"
    assert data["start_price"] == 540.50
    assert data["is_visible"] is True
    assert data["is_locked"] is False


def test_create_drawing_invalid_type(client):
    resp = client.post(
        "/api/drawing-tools",
        json={
            "symbol": "SPY",
            "timeframe": "1d",
            "drawing_type": "not_a_type",
            "start_timestamp": "2026-08-01T00:00:00",
            "start_price": 540.0,
        },
    )
    assert resp.status_code == 400
    assert "Invalid drawing_type" in resp.json()["detail"]


def test_create_drawing_invalid_line_style(client):
    resp = client.post(
        "/api/drawing-tools",
        json={
            "symbol": "AAPL",
            "timeframe": "5m",
            "drawing_type": "trend_line",
            "line_style": "invalid",
            "start_timestamp": "2026-08-01T00:00:00",
            "start_price": 200.0,
        },
    )
    assert resp.status_code == 400
    assert "Invalid line_style" in resp.json()["detail"]


def test_get_drawing_by_id(client):
    r = client.post(
        "/api/drawing-tools",
        json={
            "symbol": "AAPL",
            "timeframe": "1h",
            "drawing_type": "horizontal_line",
            "start_timestamp": "2026-08-01T00:00:00",
            "start_price": 180.0,
        },
    )
    drawing_id = r.json()["id"]
    resp = client.get(f"/api/drawing-tools/{drawing_id}")
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "AAPL"


def test_get_drawing_not_found(client):
    resp = client.get("/api/drawing-tools/99999")
    assert resp.status_code == 404


def test_update_drawing(client):
    r = client.post(
        "/api/drawing-tools",
        json={
            "symbol": "TSLA",
            "timeframe": "1d",
            "drawing_type": "trend_line",
            "label": "Old",
            "start_timestamp": "2026-08-01T00:00:00",
            "start_price": 250.0,
        },
    )
    drawing_id = r.json()["id"]
    resp = client.patch(
        f"/api/drawing-tools/{drawing_id}",
        json={"label": "New Label", "is_locked": True, "color": "#ef4444"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["label"] == "New Label"
    assert data["is_locked"] is True
    assert data["color"] == "#ef4444"


def test_delete_drawing(client):
    r = client.post(
        "/api/drawing-tools",
        json={
            "symbol": "NVDA",
            "timeframe": "15m",
            "drawing_type": "trend_line",
            "start_timestamp": "2026-08-01T00:00:00",
            "start_price": 500.0,
        },
    )
    drawing_id = r.json()["id"]
    resp = client.delete(f"/api/drawing-tools/{drawing_id}")
    assert resp.status_code == 204
    assert client.get(f"/api/drawing-tools/{drawing_id}").status_code == 404


def test_list_drawings_filter_symbol(client):
    for sym in ("AAPL", "TSLA", "AAPL"):
        client.post(
            "/api/drawing-tools",
            json={
                "symbol": sym,
                "timeframe": "1d",
                "drawing_type": "trend_line",
                "start_timestamp": "2026-08-01T00:00:00",
                "start_price": 100.0,
            },
        )
    resp = client.get("/api/drawing-tools?symbol=AAPL")
    assert resp.status_code == 200
    assert all(d["symbol"] == "AAPL" for d in resp.json())
    assert len(resp.json()) == 2


def test_list_drawings_filter_timeframe(client):
    for tf in ("1m", "1d", "1d"):
        client.post(
            "/api/drawing-tools",
            json={
                "symbol": "SPY",
                "timeframe": tf,
                "drawing_type": "trend_line",
                "start_timestamp": "2026-08-01T00:00:00",
                "start_price": 500.0,
            },
        )
    resp = client.get("/api/drawing-tools?timeframe=1d")
    assert resp.status_code == 200
    assert all(d["timeframe"] == "1d" for d in resp.json())
    assert len(resp.json()) == 2


def test_fib_retracement_with_levels(client):
    resp = client.post(
        "/api/drawing-tools",
        json={
            "symbol": "SPY",
            "timeframe": "1d",
            "drawing_type": "fib_retracement",
            "label": "Fib retracement",
            "fib_levels": "0,0.236,0.382,0.5,0.618,0.786,1.0",
            "start_timestamp": "2026-08-01T00:00:00",
            "start_price": 500.0,
            "end_timestamp": "2026-08-30T00:00:00",
            "end_price": 600.0,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["fib_levels"] == "0,0.236,0.382,0.5,0.618,0.786,1.0"
