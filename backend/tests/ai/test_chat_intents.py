from unittest.mock import Mock

from backend.ai.chat import _generate_reply
from backend.ai.tool_registry import ToolResult


def _symbol_block(symbol: str) -> dict:
    return {"symbol": symbol, "availability": {"engine_warm": True}, "context": {}}


def test_options_question_routes_to_typed_tool_without_ai(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    monkeypatch.setattr(
        "backend.ai.chat.default_registry.execute",
        lambda request: ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data={"symbol": "AAPL", "expiration": "2026-10-16"},
            provider="webull",
            session="regular",
        ),
    )

    text, grounded, _ = _generate_reply(
        None,
        [_symbol_block("AAPL")],
        [],
        None,
        [],
        "show AAPL options",
        None,
        False,
        ["AAPL"],
        {},
    )

    assert grounded is True
    assert "get_options_snapshot" in text
    complete.assert_not_called()


def test_options_question_requires_symbol_scope(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)

    text, grounded, _ = _generate_reply(
        None,
        [],
        [],
        None,
        [],
        "show options",
        None,
        False,
        [],
        {},
    )

    assert "Which ticker" in text
    assert grounded is False
    complete.assert_not_called()


def test_historical_question_uses_remembered_timeframe(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    requests = []

    def execute(request):
        requests.append(request)
        return ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data={"symbol": "MSFT", "bars": []},
            provider="MarketLens database",
            timeframe="1h",
        )

    monkeypatch.setattr("backend.ai.chat.default_registry.execute", execute)
    text, grounded, _ = _generate_reply(
        None,
        [_symbol_block("MSFT")],
        [],
        None,
        [],
        "show historical bars",
        None,
        False,
        ["MSFT"],
        {"timeframe": "1h"},
    )

    assert grounded is True
    assert "get_bars" in text
    assert requests[0].arguments["timeframe"] == "1h"
    complete.assert_not_called()


def test_why_move_routes_to_evidence_tool(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    requests = []

    def execute(request):
        requests.append(request)
        return ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data={"facts": [], "correlations": [], "unknowns": [], "conclusion": {"status": "evidence_only"}},
            provider="MarketLens composite",
        )

    monkeypatch.setattr("backend.ai.chat.default_registry.execute", execute)
    text, grounded, _ = _generate_reply(
        None,
        [_symbol_block("AAPL")],
        [],
        None,
        [],
        "why did AAPL move today?",
        None,
        False,
        ["AAPL"],
        {},
    )

    assert grounded is True
    assert "why_did_it_move" in text
    assert requests[0].tool_name == "why_did_it_move"
    complete.assert_not_called()
