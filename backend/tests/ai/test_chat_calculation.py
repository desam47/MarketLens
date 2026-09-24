from unittest.mock import Mock

from backend.ai.calculator import CalculationRequest, calculate
from backend.ai.chat import (
    _finalize_parsed,
    _format_calculation_reply,
    _generate_reply,
    _run_action,
)
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
    assert "reward/risk ratio of 2.00" in text
    assert "$5.00 per share" in text


def test_calculation_formatter_labels_break_even_and_flat_changes() -> None:
    position_pnl = CalculationRequest(
        calculation="position_pnl", entry_price=100, exit_price=100, shares=10
    )
    percentage_change = CalculationRequest(
        calculation="percentage_change", old_value=100, new_value=100
    )
    dollar_change = CalculationRequest(
        calculation="dollar_change", old_value=100, new_value=100
    )

    pnl_text = _format_calculation_reply(
        position_pnl, calculate(position_pnl).values, [], context={}, provider="MarketLens calculator"
    )
    percentage_text = _format_calculation_reply(
        percentage_change, calculate(percentage_change).values, [], context={}, provider="MarketLens calculator"
    )
    dollar_text = _format_calculation_reply(
        dollar_change, calculate(dollar_change).values, [], context={}, provider="MarketLens calculator"
    )

    assert "break-even" in pnl_text
    assert "profit" not in pnl_text
    assert "unchanged" in percentage_text
    assert "increase" not in percentage_text
    assert "unchanged" in dollar_text
    assert "increase" not in dollar_text


def test_calculation_formatter_calls_credit_spreads_a_credit() -> None:
    request = CalculationRequest(
        calculation="options_vertical_spread",
        option_type="call",
        strike=110,
        premium=2,
        short_strike=100,
        short_premium=5,
    )
    text = _format_calculation_reply(
        request, calculate(request).values, [], context={}, provider="MarketLens calculator"
    )

    assert "collects $3.00 net credit" in text
    assert "costs -$3.00" not in text


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
    assert "is 25.00% of it" in text
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

    assert "is 25.00% of it" in text
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
    assert "Webull" in text
    # 3.2s is fresh data — no age warning expected (warning only fires above 900s)
    assert "3.2s old" not in text


def test_position_size_fallback_reads_labelled_inputs_not_number_order() -> None:
    """BF-02: "risk 1% of my 10000 account, entry 50 stop 48" used to map
    by order to entry=1, stop=10000, account=50, risk=48%."""
    from backend.ai.chat import _fallback_calculation

    request = _fallback_calculation("position size: risk 1% of my 10000 account, entry 50 stop 48")

    assert request is not None
    assert (request.calculation, request.entry_price, request.stop_price, request.account_value, request.risk_percent) == (
        "position_size", 50, 48, 10000, 1,
    )


def test_position_size_fallback_does_not_guess_a_missing_label() -> None:
    from backend.ai.chat import _fallback_calculation

    assert _fallback_calculation("position size with risk 50 48 10000 1") is None


def test_risk_reward_fallback_reads_labelled_inputs() -> None:
    from backend.ai.chat import _fallback_calculation

    request = _fallback_calculation("target 110, stop 95, entry 100 - what's the risk reward?")

    assert request is not None
    assert (request.calculation, request.entry_price, request.stop_price, request.target_price) == (
        "risk_reward", 100, 95, 110,
    )


def test_date_numbers_are_not_calculator_inputs() -> None:
    """BF-03: "return from Jan 5 to Jan 20" used to become
    percentage_change(5, 20) = +300%."""
    from backend.ai.chat import _fallback_calculation

    for text in (
        "What was NVDA's return from Jan 5 to Jan 20?",
        "What was the change from 1/5 to 1/20?",
        "Return from 2026-01-05 to 2026-01-20",
    ):
        assert _fallback_calculation(text) is None, text


def test_plain_percent_change_still_uses_the_fallback() -> None:
    from backend.ai.chat import _fallback_calculation

    request = _fallback_calculation("What is the percent change from 50 to 60?")

    assert request is not None
    assert (request.calculation, request.old_value, request.new_value) == ("percentage_change", 50, 60)


def test_account_size_accepts_k_and_m_suffixes() -> None:
    """BF-02 follow-up: "$10k account" was read as no account value."""
    from backend.ai.chat import _account_value, _fallback_calculation

    request = _fallback_calculation("position size: risk 1% of my $10k account, entry 50 stop 48")
    assert request is not None
    assert (request.calculation, request.account_value) == ("position_size", 10_000)
    assert _account_value("account size 1.5m") == 1_500_000
    assert _account_value("portfolio of 25K, entry 10") == 25_000
    assert _account_value("my 10000 account") == 10_000
    # A word that starts with k/m isn't a suffix.
    assert _account_value("account 10 more shares") == 10
