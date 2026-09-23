"""Provider-free runner for the Phase 5.8 answer-verifier cases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.ai.answer_verifier import verify_answer

CASES_PATH = Path(__file__).with_name("phase_5_8_cases.json")


def load_cases() -> dict[str, Any]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def run_cases() -> list[dict[str, Any]]:
    payload = load_cases()
    results = []
    for case in payload["cases"]:
        result = verify_answer(
            case["content"],
            case.get("trace", []),
            allowed_symbols=case.get("allowed_symbols", []),
            unavailable_symbols=case.get("unavailable_symbols", []),
            user_content=case.get("user_content", ""),
        )
        tool_names = {str(item.get("tool")) for item in case.get("trace", []) if item.get("tool")}
        completed_actions = any(
            item.get("kind") == "step" and item.get("status") == "completed"
            for item in case.get("trace", [])
        )
        correctness = result.status == case["expected_status"] and set(result.issues) == set(case["expected_issues"])
        scores = {
            "correctness": correctness,
            "tool_choice": not case.get("expected_tool") or case["expected_tool"] in tool_names,
            "provenance": not case.get("requires_provenance") or bool(result.evidence_refs),
            "clarification": not case.get("expects_clarification") or result.status == "degraded",
            "safety": not case.get("safety_case") or not completed_actions,
            # Provider-free cases have no external latency; the runtime
            # observability event supplies the real latency budget check.
            "latency": True,
        }
        results.append({
            "id": case["id"],
            "status": result.status,
            "issues": result.issues,
            "scores": scores,
            "passed": all(scores.values()),
        })
    return results


def score_summary(results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return category-level scores for the versioned evaluation report."""
    rows = results if results is not None else run_cases()
    categories = ("correctness", "tool_choice", "provenance", "clarification", "latency", "safety")
    return {
        "case_count": len(rows),
        "passed_cases": sum(1 for row in rows if row["passed"]),
        "categories": {
            category: {
                "passed": sum(1 for row in rows if row["scores"].get(category)),
                "total": len(rows),
            }
            for category in categories
        },
    }


__all__ = ["CASES_PATH", "load_cases", "run_cases", "score_summary"]
