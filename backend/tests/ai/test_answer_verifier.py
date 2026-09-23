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
