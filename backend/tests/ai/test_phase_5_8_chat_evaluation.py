"""End-to-end Chat evaluation: every versioned case must pass."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from backend.ai.evaluations import chat_runner
from backend.ai.evaluations.chat_runner import chat_score_summary, load_chat_cases, run_case
from backend.ai.evaluations.export_fixtures import fixture_to_case


@pytest.mark.parametrize("case", load_chat_cases()["cases"], ids=lambda case: case["id"])
def test_chat_evaluation_case(case: dict) -> None:
    result = run_case(case)
    assert result["passed"], "\n".join(result["failures"])


def test_a_wrong_expectation_fails_the_case() -> None:
    """The harness must be able to fail; otherwise a pass means nothing."""
    case = copy.deepcopy(next(c for c in load_chat_cases()["cases"] if c["id"] == "options_route_is_deterministic"))
    case["turns"][0]["expect"]["tools"] = ["get_quote"]
    result = run_case(case)
    assert not result["passed"]
    assert result["scores"]["tool_choice"] is False


def test_unexpected_model_call_fails_tool_choice() -> None:
    case = {
        "id": "unscripted",
        "turns": [{"user": "what should I keep in mind this week?", "expect": {}}],
    }
    result = run_case(case)
    assert not result["passed"]
    assert any("unscripted model call" in failure for failure in result["failures"])


def test_summary_counts_only_applicable_categories() -> None:
    rows = [
        {"id": "a", "passed": True, "scores": {"tool_choice": True, "latency": True}, "failures": []},
        {"id": "b", "passed": False, "scores": {"safety": False, "latency": True}, "failures": ["x"]},
    ]
    summary = chat_score_summary(rows)
    assert summary["categories"]["tool_choice"] == {"passed": 1, "total": 1}
    assert summary["categories"]["safety"] == {"passed": 0, "total": 1}
    assert summary["categories"]["provenance"] == {"passed": 0, "total": 0}
    assert summary["passed_cases"] == 1


def test_regression_drafts_are_skipped_until_reviewed(tmp_path, monkeypatch) -> None:
    fixture = SimpleNamespace(
        id=7, message_id=42, prompt="how is AAPL?", response="AAPL is at $999.",
        response_blocks=json.dumps([{"type": "evidence", "data": {"symbols": {"verified": ["AAPL"]}}}]),
        rating="incorrect", category="wrong_data", comment="price was wrong", status="approved",
    )
    draft = fixture_to_case(fixture)
    assert draft["needs_review"] is True
    assert draft["symbol_universe"] == ["AAPL"]
    assert draft["turns"][0]["user"] == "how is AAPL?"
    assert draft["turns"][0]["expect"]["reply_not_contains"] == ["AAPL is at $999."]

    monkeypatch.setattr(chat_runner, "REGRESSION_DIR", tmp_path)
    (tmp_path / "fixture_7.json").write_text(json.dumps(draft), encoding="utf-8")
    reviewed = {**draft, "id": "fixture_8", "needs_review": False}
    (tmp_path / "fixture_8.json").write_text(json.dumps(reviewed), encoding="utf-8")

    runnable, pending = chat_runner.load_regression_cases()
    assert [case["id"] for case in runnable] == ["fixture_8"]
    assert pending == ["fixture_7"]
