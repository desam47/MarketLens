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
    assert blocks[2]["data"]["values"]["allocation"] == 25
    assert blocks[2]["quality"]["state"] == "verified"


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
