"""Tests for AI analysis with a custom template (Phase 2.4.5).

Verifies that ``POST /api/ai/analyze?template_id=N`` resolves the
template, renders its system prompt with the analysis context
(symbol, timeframe), and passes the rendered prompt into the
underlying ``analyze_symbol`` call as ``system_prompt_override``.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.database import Base, SessionLocal, engine
from backend.models import AITemplate


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_user_templates():
    db = SessionLocal()
    try:
        db.query(AITemplate).filter(AITemplate.is_system == False).delete()  # noqa: E712
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(AITemplate).filter(AITemplate.is_system == False).delete()  # noqa: E712
        db.commit()
    finally:
        db.close()


def _create_template(name: str, prompt: str) -> int:
    resp = TestClient(app).post(
        "/api/ai/templates",
        json={
            "name": name,
            "system_prompt": prompt,
            "variables": ["symbol", "timeframe"],
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


# ── render_template unit tests ───────────────────────────────────────────


def test_render_template_substitutes_variables():
    from backend.ai.prompt import render_template

    out = render_template(
        "Hello {{symbol}} on {{timeframe}}",
        {"symbol": "AAPL", "timeframe": "4h"},
    )
    assert out == "Hello AAPL on 4h"


def test_render_template_handles_whitespace_and_unknowns():
    from backend.ai.prompt import render_template

    out = render_template(
        "{{  symbol  }} - {{ unknown }}",
        {"symbol": "TSLA"},
    )
    assert out == "TSLA - "


def test_render_template_handles_non_string_values():
    from backend.ai.prompt import render_template

    out = render_template(
        "Count: {{n}}",
        {"n": 42},
    )
    assert out == "Count: 42"


def test_render_template_handles_none():
    from backend.ai.prompt import render_template

    out = render_template(
        "Value: {{x}}",
        {"x": None},
    )
    assert out == "Value: "


# ── Router integration with template_id ──────────────────────────────────


@patch("backend.api.ai.router.analyze_symbol")
@patch("backend.api.ai.router.ai_manager")
def test_analyze_with_template_passes_override(mock_ai_mgr, mock_analyze, client):
    from backend.ai.prompt import AnalysisResponse

    mock_analyze.return_value = AnalysisResponse(
        summary="Risk summary for the symbol.",
        trend="bullish",
        confidence=0.5,
        supporting_factors=["Above SMA 50"],
        risk_factors=["RSI overbought"],
        timeframe_conflicts=[],
        key_levels=["$200"],
    )
    mock_ai_mgr.settings.provider = "ollama"
    mock_ai_mgr.settings.model = "llama3.2"

    tmpl_id = _create_template(
        "Risk Only",
        "Just summarize the risk for {{symbol}} on {{timeframe}}.",
    )

    resp = client.post(
        f"/api/ai/analyze?symbol=MSFT&timeframe=1d&template_id={tmpl_id}",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["template_id"] == tmpl_id
    assert data["template_name"] == "Risk Only"

    mock_analyze.assert_called_once()
    override = mock_analyze.call_args[1].get("system_prompt_override")
    assert override is not None
    assert "MSFT" in override
    assert "1d" in override
    assert "Just summarize" in override


@patch("backend.api.ai.router.analyze_symbol")
@patch("backend.api.ai.router.ai_manager")
def test_analyze_without_template_uses_default(mock_ai_mgr, mock_analyze, client):
    from backend.ai.prompt import AnalysisResponse

    mock_analyze.return_value = AnalysisResponse(
        summary="Analysis of the symbol shows mixed signals.",
        trend="bullish",
        confidence=0.5,
        supporting_factors=["Above SMA 50"],
        risk_factors=["RSI overbought"],
        timeframe_conflicts=[],
        key_levels=["$200"],
    )
    mock_ai_mgr.settings.provider = "ollama"
    mock_ai_mgr.settings.model = "llama3.2"

    resp = client.post("/api/ai/analyze?symbol=AAPL&timeframe=1d")
    assert resp.status_code == 200
    data = resp.json()
    assert data["template_id"] is None
    assert data["template_name"] is None

    mock_analyze.assert_called_once()
    override = mock_analyze.call_args[1].get("system_prompt_override")
    assert override is None


def test_analyze_with_unknown_template_404(client):
    """Unknown template ID raises 404 before attempting AI analysis."""
    resp = client.post("/api/ai/analyze?symbol=AAPL&template_id=999999")
    assert resp.status_code == 404


def test_analyze_with_inactive_template_400(client):
    """An inactive template raises 400 when selected for analysis."""
    tmpl_id = _create_template("Inactive", "Just a long enough system prompt to be valid.")
    # Deactivate it
    TestClient(app).patch(
        f"/api/ai/templates/{tmpl_id}",
        json={"is_active": False},
    )
    resp = client.post(f"/api/ai/analyze?symbol=AAPL&template_id={tmpl_id}")
    assert resp.status_code == 400
    assert "inactive" in resp.json()["detail"]


@patch("backend.api.ai.router.analyze_symbol")
@patch("backend.api.ai.router.ai_manager")
def test_analyze_without_template_id_ignores_the_default_template(mock_ai_mgr, mock_analyze, client):
    """AA-15: a template marked default is a library preference only. Without an
    explicit template_id, analysis keeps the built-in prompt."""
    from backend.ai.prompt import AnalysisResponse

    mock_analyze.return_value = AnalysisResponse(
        summary="Analysis of the symbol looks neutral.",
        trend="neutral",
        confidence=0.4,
        supporting_factors=["Above SMA 50"],
        risk_factors=["RSI overbought"],
        timeframe_conflicts=[],
        key_levels=["$200"],
    )
    mock_ai_mgr.settings.provider = "ollama"
    mock_ai_mgr.settings.model = "llama3.2"

    # Create a default template
    TestClient(app).post(
        "/api/ai/templates",
        json={
            "name": "My Default",
            "system_prompt": "Default template for {{symbol}} on {{timeframe}}",
            "variables": ["symbol", "timeframe"],
            "is_default": True,
        },
    )

    resp = client.post("/api/ai/analyze?symbol=GOOG&timeframe=4h")
    assert resp.status_code == 200
    data = resp.json()
    assert data["template_name"] is None
    assert mock_analyze.call_args[1].get("system_prompt_override") is None
