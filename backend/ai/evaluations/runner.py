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
        results.append({
            "id": case["id"],
            "status": result.status,
            "issues": result.issues,
            "passed": result.status == case["expected_status"] and set(result.issues) == set(case["expected_issues"]),
        })
    return results


__all__ = ["CASES_PATH", "load_cases", "run_cases"]
