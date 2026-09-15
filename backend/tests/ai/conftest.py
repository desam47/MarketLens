"""
backend/tests/ai/ conftest — isolate build_context()/analyze_symbol()
from real aux-data network calls.

Regression found live 2026-09-09: enabling AUX_NEWS_ENABLED/
AUX_FUNDAMENTALS_ENABLED=true in the live .env (feature 1's
news/fundamentals-aware AI Analysis needed them on to do anything)
meant every test in this directory that calls build_context() or
analyze_symbol() without its own aux_data_manager mock started making
1-2 REAL HTTP calls to Yahoo Finance per test — Settings() reads the
same live .env this whole test process does. Confirmed: a
pre-existing test (test_phase16_analyze.py::TestBuildContext::
test_context_for_known_symbol) went from near-instant to ~7s once the
flags were flipped, and this file's own new tests
(test_context_transitions.py, test_context_divergence.py) were
similarly affected — slow, and coupled to external network
availability/Yahoo's API being reachable, neither of which a unit
test should depend on.

This autouse fixture patches aux_data_manager to a safe, instant,
disabled-shaped default for every test in this directory. Tests that
specifically want to exercise the news/fundamentals path
(test_context_news_fundamentals.py) apply their own `@patch` on the
same target, which nests correctly inside this fixture's patch and
takes precedence for their duration — this fixture's default never
overrides a test's own explicit mock.
"""
from unittest.mock import patch

import pytest

from backend.models.aux_data import FundamentalsItem, FundamentalsResponse, NewsResponse
from backend.utils.timezone import now_ny


@pytest.fixture(autouse=True)
def _disable_real_aux_data_calls():
    with patch("backend.aux_data.services.manager.aux_data_manager") as mock_mgr:
        mock_mgr.get_news.return_value = NewsResponse(
            symbol="TEST", items=[], provider="disabled", timestamp=now_ny(),
        )
        mock_mgr.get_fundamentals.return_value = FundamentalsResponse(
            symbol="TEST", data=FundamentalsItem(symbol="TEST"),
            provider="disabled", timestamp=now_ny(),
        )
        yield


@pytest.fixture(autouse=True)
def _clear_analysis_cache():
    """O4: clear the short-term analysis cache between tests so cached
    results from one test don't leak into another."""
    from backend.ai.analyze import _clear_analysis_cache

    _clear_analysis_cache()
    yield
    _clear_analysis_cache()
