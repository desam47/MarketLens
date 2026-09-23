from __future__ import annotations

import pytest

from backend.ai.answer_verifier import VERIFIER_VERSION, assign_evidence_ids, verify_answer
from backend.ai.evaluations.runner import load_cases, run_cases, score_summary


def test_evidence_ids_are_stable_and_exclude_action_steps() -> None:
    trace = [
        {"kind": "step", "tool": "delete_watchlist", "status": "pending_confirmation"},
        {"tool": "get_quote", "ok": True},
        {"tool": "get_bars", "ok": False},
    ]
    assign_evidence_ids(trace)
    assert trace[0].get("evidence_id") is None
    assert trace[1]["evidence_id"] == "ev-1"
    assert trace[2]["evidence_id"] == "ev-2"


def test_generic_tool_reply_can_verify_freshness_metadata() -> None:
    trace = [
        {
            "tool": "compare_symbols",
            "ok": True,
            "evidence_id": "ev-1",
            "freshness_seconds": 1.0,
            "source_timestamp": "2026-09-23T19:00:00+00:00",
            "session": "all",
            "evidence_values": {
                "freshness_seconds": 1.0,
                "data.rankings[0].value": 12.5,
                "data.rankings[1].value": 4.2,
                "data.evaluated_count": 2,
            },
        }
    ]
    content = (
        "Verified compare_symbols result from MarketLens comparison "
        "(1.0s old, session all, timeframe not specified): "
        "AAPL ranked above MSFT at 12.5 versus 4.2."
    )

    result = verify_answer(
        content,
        trace,
        allowed_symbols=["AAPL", "MSFT"],
        user_content="Compare AAPL and MSFT",
    )

    assert result.status == "verified"
    assert result.issues == []


@pytest.mark.parametrize("case", load_cases()["cases"], ids=lambda case: case["id"])
def test_phase_5_8_versioned_evaluation_case(case: dict) -> None:
    result = verify_answer(
        case["content"],
        case.get("trace", []),
        allowed_symbols=case.get("allowed_symbols", []),
        unavailable_symbols=case.get("unavailable_symbols", []),
        user_content=case.get("user_content", ""),
    )
    assert result.version == VERIFIER_VERSION
    assert result.status == case["expected_status"]
    assert set(result.issues) == set(case["expected_issues"])


def test_runner_reports_all_versioned_cases_as_passing() -> None:
    results = run_cases()
    assert len(results) == len(load_cases()["cases"])
    assert all(result["passed"] for result in results)
    summary = score_summary(results)
    assert summary["passed_cases"] == summary["case_count"]
    assert set(summary["categories"]) == {
        "correctness", "tool_choice", "provenance", "clarification", "latency", "safety",
    }


def test_common_finance_acronyms_are_not_unknown_tickers() -> None:
    result = verify_answer(
        "The MACD and ETF context is mixed.",
        [{"tool": "get_trend", "ok": True, "freshness_seconds": 2, "evidence_values": {}}],
        allowed_symbols=["AAPL"],
    )
    assert result.issues == []


def test_numeric_claims_are_scoped_to_the_named_symbol_when_available() -> None:
    trace = [
        {"tool": "get_quote", "ok": True, "symbol": "AAPL", "evidence_values": {"price": 100}, "freshness_seconds": 2},
        {"tool": "get_quote", "ok": True, "symbol": "MSFT", "evidence_values": {"price": 200}, "freshness_seconds": 2},
    ]
    result = verify_answer(
        "AAPL is at $200 and MSFT is at $200.",
        trace,
        allowed_symbols=["AAPL", "MSFT"],
    )
    assert result.status == "blocked"
    assert result.issues == ["unsupported_numeric_claim"]


def test_source_timestamp_alone_does_not_prove_live_freshness() -> None:
    result = verify_answer(
        "The latest AAPL price is $101.",
        [{"tool": "get_quote", "ok": True, "source_timestamp": "2026-09-23T19:00:00+00:00", "evidence_values": {"price": 101}}],
        allowed_symbols=["AAPL"],
    )
    assert result.status == "blocked"
    assert result.issues == ["live_claim_without_freshness"]
