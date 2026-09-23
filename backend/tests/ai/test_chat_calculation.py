from unittest.mock import Mock

from backend.ai.chat import _finalize_parsed, _generate_reply, _run_action
from backend.ai.prompt import ChatReplyResponse
from backend.ai.tool_registry import ToolResult


def test_chat_calculate_action_uses_verified_backend_values() -> None:
    parsed = ChatReplyResponse(
        reply="placeholder",
        grounded=True,
        action="calculate",
        action_calculation={
            "calculation": "risk_reward",
            "entry_price": 100,
            "stop_price": 95,
            "target_price": 110,
        },
    )

    text, grounded, screened = _run_action(None, parsed)

    assert grounded is True
    assert screened == []
    assert "risk_reward=2.0" in text
    assert "Formula:" in text


def test_chat_fallback_parses_unambiguous_allocation_question() -> None:
    parsed = ChatReplyResponse(reply="placeholder", grounded=True)

    text, grounded, screened = _finalize_parsed(
        None,
        parsed,
        [],
        user_content="A position is worth $25,000 in a $100,000 portfolio. What is its allocation?",
    )

    assert grounded is True
    assert screened == []
    assert "allocation_percent=25.0" in text
    assert parsed.action == "calculate"


def test_chat_asks_for_missing_calculation_inputs_without_ai_call(monkeypatch) -> None:
    mock_complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", mock_complete)

    text, grounded, screened = _generate_reply(
        None,
        [],
        [],
        None,
        [],
        "what is my allocation?",
        None,
        False,
        [],
        {},
    )

    assert "What values" in text
    assert grounded is False
    assert screened == []
    mock_complete.assert_not_called()


def test_chat_can_reuse_previous_calculation_inputs(monkeypatch) -> None:
    mock_complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", mock_complete)

    text, grounded, screened = _generate_reply(
        None,
        [],
        [],
        None,
        [],
        "use the previous values to calculate the allocation",
        None,
        False,
        [],
        {
            "last_calculation_inputs": {
                "calculation": "allocation",
                "position_value": 25000,
                "portfolio_value": 100000,
            }
        },
    )

    assert "allocation_percent=25.0" in text
    assert grounded is True
    assert screened == []
    mock_complete.assert_not_called()


def test_chat_market_tool_action_returns_provenance(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.ai.chat.default_registry.execute",
        lambda request: ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data={"symbol": "AAPL", "price": 220},
            provider="webull",
            source_timestamp="2026-09-22T12:00:00-04:00",
            freshness_seconds=3.2,
            session="regular",
            timeframe="1d",
        ),
    )
    parsed = ChatReplyResponse(
        reply="placeholder",
        grounded=True,
        action="get_quote",
        action_tool_arguments={"symbol": "AAPL", "session": "regular"},
    )

    text, grounded, screened = _run_action(None, parsed)

    assert grounded is True
    assert screened == []
    assert "webull" in text
    assert "3.2s old" in text
