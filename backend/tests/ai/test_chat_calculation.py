from backend.ai.chat import _finalize_parsed, _run_action
from backend.ai.prompt import ChatReplyResponse


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
