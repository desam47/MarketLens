"""Tests for the background AI jobs API (Phase 2.5)."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.database import Base, engine


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    return TestClient(app)


# In-memory fake: map job_id -> dict
_FAKE_JOBS: dict[str, dict] = {}
_MOCK_JOB_COUNTER = 0


@pytest.fixture(autouse=True)
def _patch_background():
    """Stub out Redis/RQ so tests don't need a live Redis instance.

    We mock ``enqueue_analyze_job`` directly rather than the lower-level
    ``get_queue`` because ``TestClient`` runs in a subprocess where
    ``monkeypatch`` cannot reach.
    """
    global _FAKE_JOBS
    _FAKE_JOBS.clear()

    def fake_enqueue(symbol, timeframe, template_id, template_name):
        global _MOCK_JOB_COUNTER
        _MOCK_JOB_COUNTER += 1
        job_id = f"mock-rq-job-{_MOCK_JOB_COUNTER:04d}"
        return job_id

    def fake_get_status(job_id: str):
        return _FAKE_JOBS.get(job_id)

    with patch("backend.api.ai.jobs.enqueue_analyze_job", side_effect=fake_enqueue), \
         patch("backend.api.ai.jobs.get_job_status", side_effect=fake_get_status):
        yield


# ── Helpers ────────────────────────────────────────────────────────────────


def _enqueue_and_get_job_id(client, symbol="AAPL", timeframe="1d", template_id=None):
    payload = {"symbol": symbol, "timeframe": timeframe}
    if template_id is not None:
        payload["template_id"] = template_id
    resp = client.post("/api/ai/jobs", json=payload)
    assert resp.status_code == 202, resp.json()
    return resp.json()["job_id"]


# ── Enqueue ────────────────────────────────────────────────────────────────


def test_enqueue_job_returns_202_and_job_id(client):
    resp = client.post("/api/ai/jobs", json={"symbol": "TSLA", "timeframe": "4h"})
    assert resp.status_code == 202
    data = resp.json()
    assert data["job_id"].startswith("mock-rq-job-")
    assert data["status"] == "queued"
    assert data["symbol"] == "TSLA"
    assert data["timeframe"] == "4h"
    assert data["template_id"] is None
    assert data["template_name"] is None


def test_enqueue_job_with_template(client):
    from backend.database import SessionLocal
    from backend.models import AITemplate

    db = SessionLocal()
    try:
        t = AITemplate(
            name="Risk Focus",
            system_prompt="Summarize risk for {{symbol}}.",
            variables_json='["symbol"]',
            is_active=True,
        )
        db.add(t)
        db.commit()
        tmpl_id = t.id
    finally:
        db.close()

    resp = client.post(
        "/api/ai/jobs",
        json={"symbol": "MSFT", "timeframe": "1d", "template_id": tmpl_id},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["template_id"] == tmpl_id
    assert data["template_name"] == "Risk Focus"


def test_enqueue_job_unknown_template_404(client):
    resp = client.post(
        "/api/ai/jobs",
        json={"symbol": "AAPL", "template_id": 999999},
    )
    assert resp.status_code == 404


def test_enqueue_job_inactive_template_400(client):
    from backend.database import SessionLocal
    from backend.models import AITemplate

    db = SessionLocal()
    try:
        t = AITemplate(
            name="Inactive",
            system_prompt="Do something for {{symbol}}.",
            variables_json='["symbol"]',
            is_active=False,
        )
        db.add(t)
        db.commit()
        tmpl_id = t.id
    finally:
        db.close()

    resp = client.post(
        "/api/ai/jobs",
        json={"symbol": "AAPL", "template_id": tmpl_id},
    )
    assert resp.status_code == 400
    assert "inactive" in resp.json()["detail"]


def test_enqueue_job_queue_unavailable_503(client):
    """When enqueue returns None (Redis down), POST /api/ai/jobs returns 503."""
    with patch("backend.api.ai.jobs.enqueue_analyze_job", return_value=None):
        resp = client.post("/api/ai/jobs", json={"symbol": "AAPL"})
    assert resp.status_code == 503
    assert "Redis" in resp.json()["detail"] or "unavailable" in resp.json()["detail"]


# ── Get status ─────────────────────────────────────────────────────────────


def test_get_job_404_when_unknown(client):
    resp = client.get("/api/ai/jobs/nonexistent-job-id")
    assert resp.status_code == 404


def test_get_job_returns_queued_status(client):
    job_id = _enqueue_and_get_job_id(client, "AAPL")

    _FAKE_JOBS[job_id] = {
        "id": 1,
        "job_id": job_id,
        "status": "queued",
        "result": None,
        "error": None,
        "symbol": "AAPL",
        "timeframe": "1d",
        "template_id": None,
        "template_name": None,
        "created_at": "2024-01-01T00:00:00Z",
        "started_at": None,
        "completed_at": None,
    }

    resp = client.get(f"/api/ai/jobs/{job_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert data["symbol"] == "AAPL"


def test_get_job_returns_finished_with_result(client):
    job_id = _enqueue_and_get_job_id(client, "MSFT", "4h")

    _FAKE_JOBS[job_id] = {
        "id": 2,
        "job_id": job_id,
        "status": "finished",
        "result": {
            "summary": "Bullish analysis for MSFT on 4h shows upward momentum.",
            "trend": "bullish",
            "confidence": 0.72,
            "supporting_factors": ["Above SMA 50", "Volume increasing"],
            "risk_factors": ["RSI at 70 — overbought"],
            "timeframe_conflicts": [],
            "key_levels": ["$420", "$415"],
            "provider": "ollama",
            "model": "llama3.2",
            "is_uncertain": False,
            "template_id": None,
            "template_name": None,
        },
        "error": None,
        "symbol": "MSFT",
        "timeframe": "4h",
        "template_id": None,
        "template_name": None,
        "created_at": "2024-01-01T00:00:00Z",
        "started_at": "2024-01-01T00:00:05Z",
        "completed_at": "2024-01-01T00:00:10Z",
    }

    resp = client.get(f"/api/ai/jobs/{job_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "finished"
    assert data["result"]["summary"] == "Bullish analysis for MSFT on 4h shows upward momentum."
    assert data["result"]["trend"] == "bullish"


def test_get_job_returns_failed_with_error(client):
    job_id = _enqueue_and_get_job_id(client, "GOOG")

    _FAKE_JOBS[job_id] = {
        "id": 3,
        "job_id": job_id,
        "status": "failed",
        "result": None,
        "error": "AI provider unavailable: connection refused",
        "symbol": "GOOG",
        "timeframe": "1d",
        "template_id": None,
        "template_name": None,
        "created_at": "2024-01-01T00:00:00Z",
        "started_at": "2024-01-01T00:00:01Z",
        "completed_at": "2024-01-01T00:00:30Z",
    }

    resp = client.get(f"/api/ai/jobs/{job_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    assert "unavailable" in data["error"]
