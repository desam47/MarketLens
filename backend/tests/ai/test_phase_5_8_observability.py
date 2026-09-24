from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

from backend.ai.chat import _complete_and_parse
from backend.ai.chat_model import CHAT_PARSE_MAX_ATTEMPTS
from backend.ai.chat_observability import (
    build_turn_observability,
    sanitize_arguments,
    sanitize_error_message,
    sanitize_warnings,
)
from backend.ai.evaluations.runner import CASES_PATH


def test_sanitized_arguments_remove_secrets_and_private_prose() -> None:
    value = sanitize_arguments({
        "symbol": "AAPL",
        "api_key": "do-not-store",
        "journal_entry": "private thesis text",
        "entry": "private journal prose",
        "nested": {"token": "also-secret"},
    })
    assert value == {
        "symbol": "AAPL",
        "api_key": "[redacted]",
        "journal_entry": "[private omitted]",
        "entry": "[private omitted]",
        "nested": {"token": "[redacted]"},
    }


def test_error_sanitizer_maps_failures_and_drops_provider_payloads() -> None:
    raw = 'Traceback (most recent call last): provider returned {"api_key":"secret"}'
    assert sanitize_error_message(raw, failure_kind="provider_exception") == (
        "The AI provider is unavailable right now. Please retry."
    )
    assert "secret" not in sanitize_error_message(raw)
    assert sanitize_error_message("Watchlist \"Missing\" not found.") == 'Watchlist "Missing" not found.'


def test_warning_sanitizer_bounds_and_redacts_raw_provider_warnings() -> None:
    warnings = sanitize_warnings([
        "Provider data status: STALE.",
        'gateway response body {"token":"secret"}',
    ])
    assert warnings[0] == "Provider data status: STALE."
    assert warnings[1] == "The tool returned a data-quality warning."


def test_turn_observability_counts_calls_retries_failures_and_evidence() -> None:
    trace = [
        {"kind": "model_call", "attempt": 1, "ok": False, "failure_kind": "parse_error", "provider_request_count": 2},
        {"kind": "model_call", "attempt": 2, "ok": True, "provider_request_count": 1, "prompt_chars": 400},
        {"tool": "get_quote", "ok": True, "provider": "webull", "duration_ms": 12, "provider_request_count": 1, "evidence_id": "ev-1"},
    ]
    result = build_turn_observability(trace, started_at=0.0, prompt_chars=400)
    assert result["model_calls"] == 2
    assert result["tool_calls"] == 1
    assert result["provider_requests"] == 4
    assert result["retry_count"] == 1
    assert result["failure_kinds"] == ["parse_error"]
    assert result["evidence_refs"] == ["ev-1"]
    assert result["target_class"] == "provider_backed"


def test_phase_5_8_failure_matrix_is_versioned_and_retries_are_bounded() -> None:
    path = Path(CASES_PATH).with_name("phase_5_8_failure_matrix.json")
    matrix = json.loads(path.read_text(encoding="utf-8"))
    assert matrix["version"] == "5.8.1"
    assert matrix["retry_budget"]["chat_parse_attempts"] == CHAT_PARSE_MAX_ATTEMPTS
    assert all(case["max_provider_requests"] <= CHAT_PARSE_MAX_ATTEMPTS for case in matrix["cases"])
    failures = {case["failure"] for case in matrix["cases"]}
    assert {"tool_deadline_exceeded", "model_unavailable"} <= failures


def test_failure_matrix_chat_cases_exist_in_the_end_to_end_suite() -> None:
    from backend.ai.evaluations.chat_runner import load_chat_cases

    matrix = json.loads(Path(CASES_PATH).with_name("phase_5_8_failure_matrix.json").read_text(encoding="utf-8"))
    chat_ids = {case["id"] for case in load_chat_cases()["cases"]}
    linked = [case["chat_case"] for case in matrix["cases"] if "chat_case" in case]
    assert linked and set(linked) <= chat_ids


def test_complete_and_parse_records_a_bounded_retry(monkeypatch) -> None:
    trace: list[dict] = []
    responses = [Mock(text="not json", provider="primary", model="m1", attempted_providers=["primary"]), Mock(
        text='{"reply":"safe","grounded":false}', provider="fallback", model="m2", attempted_providers=["primary", "fallback"]
    )]
    def fake_run_sync(awaitable):
        awaitable.close()
        return responses.pop(0)

    monkeypatch.setattr("backend.ai.chat_model.run_sync", fake_run_sync)
    parsed, failure = _complete_and_parse("short prompt", "system", 100, "primary", "fallback", trace=trace)
    assert failure is None
    assert parsed.reply == "safe"
    calls = [item for item in trace if item["kind"] == "model_call"]
    assert len(calls) == 2
    assert calls[0]["failure_kind"] == "parse_error"
    assert calls[1]["provider_request_count"] == 2
