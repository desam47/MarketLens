from __future__ import annotations

from backend.ai.response_blocks import build_response_blocks


def test_builds_calculation_evidence_and_followup_blocks() -> None:
    blocks = build_response_blocks(
        content="Verified calculation: allocation=25.",
        grounded=True,
        focus=["AAPL"],
        partial=[],
        unavailable=[],
        trace=[
            {
                "kind": "calculation",
                "tool": "calculate",
                "ok": True,
                "provider": "MarketLens",
                "source_timestamp": "2026-09-23T10:00:00",
                "entitlement": "verified",
                "data": {"values": {"allocation": 25}, "formulas": ["25000 / 100000"]},
            }
        ],
    )

    assert [block["type"] for block in blocks] == [
        "prose",
        "evidence",
        "calculation",
        "suggested_followups",
    ]
    assert blocks[1]["data"]["symbols"]["verified"] == ["AAPL"]
    assert blocks[1]["data"]["items"][0]["entitlement"] == "verified"
    assert blocks[2]["data"]["values"]["allocation"] == 25
    assert blocks[2]["quality"]["state"] == "verified"
    assert blocks[2]["quality"]["entitlement"] == "verified"


def test_partial_and_unavailable_data_become_explicit_warning() -> None:
    blocks = build_response_blocks(
        content="I only have partial coverage.",
        grounded=False,
        focus=[],
        partial=["RIVN"],
        unavailable=["ZZZZ"],
    )

    warning = next(block for block in blocks if block["type"] == "warning")
    assert warning["quality"]["state"] == "partial"
    assert any("ZZZZ" in item for item in warning["data"]["items"])


def test_regeneration_metadata_is_persisted_in_evidence() -> None:
    blocks = build_response_blocks(
        content="Refreshed answer.", grounded=True, focus=["AAPL"], partial=[], unavailable=[],
        regeneration={"mode": "refresh", "reused_context": False},
    )
    evidence = next(block for block in blocks if block["type"] == "evidence")
    assert evidence["data"]["regeneration"] == {"mode": "refresh", "reused_context": False}


def test_old_evidence_is_marked_stale_by_the_backend_contract() -> None:
    blocks = build_response_blocks(
        content="Old answer.", grounded=True, focus=["AAPL"], partial=[], unavailable=[],
        trace=[{
            "tool": "get_quote", "ok": True, "provider": "test",
            "source_timestamp": "2020-01-01T00:00:00Z", "freshness_seconds": 99999,
        }],
    )
    assert blocks[0]["quality"]["state"] == "stale"
    assert blocks[0]["quality"]["freshness_status"] == "stale"
    evidence = next(block for block in blocks if block["type"] == "evidence")
    assert evidence["data"]["items"][0]["freshness_status"] == "stale"


def test_visual_payload_is_persistable_and_keeps_quality_metadata() -> None:
    blocks = build_response_blocks(
        content="Here is the latest chart.",
        grounded=True,
        focus=["AAPL"],
        partial=[],
        unavailable=[],
        trace=[
            {
                "tool": "get_bars",
                "ok": True,
                "provider": "webull",
                "visual_type": "chart",
                "visual_data": {"symbol": "AAPL", "bars": [{"close": 100}, {"close": 101}]},
            }
        ],
    )

    chart = next(block for block in blocks if block["type"] == "chart")
    assert chart["data"]["symbol"] == "AAPL"
    assert chart["quality"]["provider"] == "webull"


def test_report_and_journal_save_payloads_are_typed_blocks() -> None:
    blocks = build_response_blocks(
        content="Saved locally.",
        grounded=True,
        focus=["AAPL"],
        partial=[],
        unavailable=[],
        trace=[
            {
                "tool": "export_report",
                "ok": True,
                "provider": "MarketLens report export",
                "visual_type": "report",
                "visual_data": {
                    "title": "Trade Plan — AAPL",
                    "content": "# Trade Plan — AAPL",
                    "deep_links": {"symbol": "#symbol", "journal": "#journal"},
                },
            },
            {
                "tool": "save_to_journal",
                "ok": True,
                "provider": "MarketLens local journal",
                "visual_type": "journal_save",
                "visual_data": {"saved_entry": {"id": "entry-1", "symbol": "AAPL"}},
            },
        ],
    )

    assert next(block for block in blocks if block["type"] == "report")["data"]["deep_links"]["symbol"] == "#symbol"
    assert next(block for block in blocks if block["type"] == "journal_save")["data"]["saved_entry"]["id"] == "entry-1"


def test_action_confirmation_block_carries_tool_specific_detail() -> None:
    """5.7.2: action_confirmation must carry the action's own detail
    (chat.py's _action_step_detail), not just tool/status, so the UI can
    show what an alert/watchlist action actually targeted."""
    blocks = build_response_blocks(
        content="Done — alert set.",
        grounded=True,
        focus=["AAPL"],
        partial=[],
        unavailable=[],
        trace=[
            {
                "kind": "step",
                "tool": "create_alert",
                "status": "completed",
                "detail": {"symbol": "AAPL", "condition_type": "price_above", "parameter": "200", "label": None, "target_id": None},
            },
        ],
    )
    action_block = next(block for block in blocks if block["type"] == "action_confirmation")
    action = action_block["data"]["actions"][0]
    assert action["tool"] == "create_alert"
    assert action["status"] == "completed"
    assert action["detail"]["symbol"] == "AAPL"
    assert action["detail"]["condition_type"] == "price_above"


def test_action_confirmation_block_handles_missing_detail() -> None:
    """An action step with no detail (e.g. a non-CRUD action, or an older
    trace shape) must still produce a valid block, not raise."""
    blocks = build_response_blocks(
        content="Saved.",
        grounded=True,
        focus=["AAPL"],
        partial=[],
        unavailable=[],
        trace=[{"kind": "step", "tool": "save_to_journal", "status": "completed"}],
    )
    action_block = next(block for block in blocks if block["type"] == "action_confirmation")
    assert action_block["data"]["actions"][0]["detail"] is None


def _followups(blocks: list[dict]) -> list[str]:
    return next(block for block in blocks if block["type"] == "suggested_followups")["data"]["items"]


def test_followups_are_the_baseline_two_with_no_preferences() -> None:
    blocks = build_response_blocks(content="ok", grounded=True, focus=[], partial=[], unavailable=[])
    assert _followups(blocks) == ["Show the source data", "Recheck with current data"]


def test_followups_are_unchanged_when_preferences_has_no_mode() -> None:
    blocks = build_response_blocks(
        content="ok", grounded=True, focus=[], partial=[], unavailable=[],
        preferences={"risk_per_trade_percent": 1.0},
    )
    assert _followups(blocks) == ["Show the source data", "Recheck with current data"]


def test_day_trading_mode_adds_short_timeframe_followup() -> None:
    blocks = build_response_blocks(
        content="ok", grounded=True, focus=[], partial=[], unavailable=[],
        preferences={"mode": "day_trading"},
    )
    assert _followups(blocks) == ["Show the source data", "Recheck with current data", "Check the 1m/5m trend"]


def test_swing_trading_mode_adds_daily_4h_followup() -> None:
    blocks = build_response_blocks(
        content="ok", grounded=True, focus=[], partial=[], unavailable=[],
        preferences={"mode": "swing_trading"},
    )
    assert _followups(blocks)[-1] == "Check the daily/4h trend"


def test_options_mode_adds_options_chain_followup() -> None:
    blocks = build_response_blocks(
        content="ok", grounded=True, focus=[], partial=[], unavailable=[],
        preferences={"mode": "options"},
    )
    assert _followups(blocks)[-1] == "Check the options chain"


def test_long_term_investing_mode_adds_fundamentals_followup() -> None:
    blocks = build_response_blocks(
        content="ok", grounded=True, focus=[], partial=[], unavailable=[],
        preferences={"mode": "long_term_investing"},
    )
    assert _followups(blocks)[-1] == "Check fundamentals"


def test_unknown_mode_value_does_not_crash_and_adds_nothing() -> None:
    """Defensive: a malformed/unrecognized mode must degrade quietly, not
    raise or add a bogus suggestion — this is deterministic lookup code
    that runs on every single Chat turn."""
    blocks = build_response_blocks(
        content="ok", grounded=True, focus=[], partial=[], unavailable=[],
        preferences={"mode": "not_a_real_mode"},
    )
    assert _followups(blocks) == ["Show the source data", "Recheck with current data"]


def test_preferences_never_touch_calculation_or_evidence_blocks() -> None:
    """The core safety invariant (5.7.3): preferences may only change
    which follow-up is suggested — never a verified number."""
    trace = [
        {
            "kind": "calculation",
            "tool": "calculate",
            "ok": True,
            "provider": "MarketLens",
            "data": {"values": {"allocation": 25}, "formulas": ["25000 / 100000"]},
        },
    ]
    no_prefs = build_response_blocks(
        content="Verified calculation: allocation=25.", grounded=True, focus=["AAPL"], partial=[], unavailable=[], trace=trace,
    )
    with_prefs = build_response_blocks(
        content="Verified calculation: allocation=25.", grounded=True, focus=["AAPL"], partial=[], unavailable=[], trace=trace,
        preferences={"mode": "day_trading", "risk_per_trade_percent": 2.5},
    )
    calc_no_prefs = next(b for b in no_prefs if b["type"] == "calculation")
    calc_with_prefs = next(b for b in with_prefs if b["type"] == "calculation")
    assert calc_no_prefs == calc_with_prefs
    # Only the trailing suggested_followups block may differ.
    assert _followups(no_prefs) != _followups(with_prefs)
