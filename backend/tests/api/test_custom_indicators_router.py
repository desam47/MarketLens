"""Tests for the custom-indicators API router."""

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.database import Base, engine
from backend.models import CustomIndicator


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_indicators():
    """Wipe the custom_indicators table before/after each test."""
    from backend.database import SessionLocal

    db = SessionLocal()
    try:
        db.query(CustomIndicator).delete()
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(CustomIndicator).delete()
        db.commit()
    finally:
        db.close()


def test_list_indicators_empty(client):
    resp = client.get("/api/custom-indicators")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_indicator(client):
    resp = client.post(
        "/api/custom-indicators",
        json={
            "name": "Test SMA",
            "slug": "test-sma",
            "formula_type": "sma",
            "parameters": {"period": 20},
            "color": "#3b82f6",
            "line_width": 1.5,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "test-sma"
    assert data["formula_type"] == "sma"
    assert data["parameters"] == {"period": 20}
    assert data["color"] == "#3b82f6"
    assert data["is_active"] is True


def test_create_indicator_invalid_formula(client):
    resp = client.post(
        "/api/custom-indicators",
        json={
            "name": "Bad",
            "slug": "bad",
            "formula_type": "not_a_real_formula",
        },
    )
    assert resp.status_code == 400
    assert "Invalid formula_type" in resp.json()["detail"]


def test_create_indicator_duplicate_slug(client):
    payload = {
        "name": "Dup",
        "slug": "dup",
        "formula_type": "sma",
        "parameters": {},
    }
    r1 = client.post("/api/custom-indicators", json=payload)
    assert r1.status_code == 201
    r2 = client.post("/api/custom-indicators", json=payload)
    assert r2.status_code == 409


def test_get_indicator_by_id(client):
    r = client.post(
        "/api/custom-indicators",
        json={"name": "X", "slug": "x", "formula_type": "rsi", "parameters": {"period": 14}},
    )
    indicator_id = r.json()["id"]
    resp = client.get(f"/api/custom-indicators/{indicator_id}")
    assert resp.status_code == 200
    assert resp.json()["slug"] == "x"


def test_get_indicator_by_slug(client):
    client.post(
        "/api/custom-indicators",
        json={
            "name": "X",
            "slug": "by-slug-test",
            "formula_type": "ema",
            "parameters": {"period": 12},
        },
    )
    resp = client.get("/api/custom-indicators/by-slug/by-slug-test")
    assert resp.status_code == 200
    assert resp.json()["formula_type"] == "ema"


def test_get_indicator_not_found(client):
    resp = client.get("/api/custom-indicators/99999")
    assert resp.status_code == 404


def test_update_indicator(client):
    r = client.post(
        "/api/custom-indicators",
        json={"name": "Y", "slug": "y", "formula_type": "sma", "parameters": {"period": 20}},
    )
    indicator_id = r.json()["id"]
    resp = client.patch(
        f"/api/custom-indicators/{indicator_id}",
        json={"name": "Updated", "parameters": {"period": 50}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Updated"
    assert data["parameters"] == {"period": 50}


def test_delete_indicator(client):
    r = client.post(
        "/api/custom-indicators",
        json={"name": "Z", "slug": "z", "formula_type": "sma", "parameters": {}},
    )
    indicator_id = r.json()["id"]
    resp = client.delete(f"/api/custom-indicators/{indicator_id}")
    assert resp.status_code == 204
    # Verify gone
    assert client.get(f"/api/custom-indicators/{indicator_id}").status_code == 404


def test_list_filters_active(client):
    client.post(
        "/api/custom-indicators",
        json={"name": "Active", "slug": "active", "formula_type": "sma", "parameters": {}},
    )
    r = client.post(
        "/api/custom-indicators",
        json={"name": "Inactive", "slug": "inactive", "formula_type": "sma", "parameters": {}},
    )
    indicator_id = r.json()["id"]
    client.patch(f"/api/custom-indicators/{indicator_id}", json={"is_active": False})

    # active_only=True (default)
    resp = client.get("/api/custom-indicators?active_only=true")
    assert all(ind["is_active"] for ind in resp.json())

    # active_only=False
    resp = client.get("/api/custom-indicators?active_only=false")
    assert len(resp.json()) == 2
