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


def test_what_changed_routes_to_comparison_tool(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    requests = []

    def execute(request):
        requests.append(request)
        return ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data={"changes": [{"type": "price", "percent": 2.0}], "unknowns": []},
            provider="MarketLens comparison",
        )

    monkeypatch.setattr("backend.ai.chat.default_registry.execute", execute)
    text, grounded, _ = _generate_reply(
        None,
        [_symbol_block("AAPL")],
        [],
        None,
        [],
        "what changed since yesterday for AAPL?",
        None,
        False,
        ["AAPL"],
        {},
    )

    assert grounded is True
    assert "what_changed" in text
    assert requests[0].arguments["reference"] == "yesterday"
    complete.assert_not_called()


def test_comparison_routes_to_ranking_tool(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    requests = []

    def execute(request):
        requests.append(request)
        return ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data={"rankings": [{"symbol": "NVDA", "rank": 1}], "unknowns": []},
            provider="MarketLens comparison",
        )

    monkeypatch.setattr("backend.ai.chat.default_registry.execute", execute)
    text, grounded, _ = _generate_reply(
        None,
        [_symbol_block("AAPL"), _symbol_block("MSFT")],
        [],
        None,
        [],
        "compare AAPL with MSFT by volatility",
        None,
        False,
        ["AAPL", "MSFT"],
        {},
    )

    assert grounded is True
    assert "compare_symbols" in text
    assert requests[0].arguments["metric"] == "volatility_percent"
    complete.assert_not_called()


def test_scenario_question_routes_to_scenario_tool(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    requests = []

    def execute(request):
        requests.append(request)
        return ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data={"available": False, "reason": "snapshot required"},
            provider="MarketLens calculator",
        )

    monkeypatch.setattr("backend.ai.chat.default_registry.execute", execute)
    text, grounded, _ = _generate_reply(
        None,
        [_symbol_block("AAPL")],
        [],
        None,
        [],
        "what if AAPL drops 5%?",
        None,
        False,
        ["AAPL"],
        {},
    )

    assert grounded is True
    assert "scenario_analysis" in text
    assert requests[0].arguments["price_shocks"] == {"AAPL": -5.0}
    complete.assert_not_called()


def test_historical_similarity_routes_to_typed_tool(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    requests = []

    def execute(request):
        requests.append(request)
        return ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data={"matches": [], "summaries": [], "look_ahead_safe": True},
            provider="MarketLens similarity",
        )

    monkeypatch.setattr("backend.ai.chat.default_registry.execute", execute)
    text, grounded, _ = _generate_reply(
        None,
        [_symbol_block("AAPL")],
        [],
        None,
        [],
        "show prior situations similar to AAPL",
        None,
        False,
        ["AAPL"],
        {},
    )

    assert grounded is True
    assert "historical_similarity" in text
    assert requests[0].arguments["symbol"] == "AAPL"
    complete.assert_not_called()


def test_signal_explanation_routes_to_typed_tool(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    requests = []

    def execute(request):
        requests.append(request)
        return ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data={"direction": "bullish", "triggers": [], "unknowns": []},
            provider="MarketLens signal explanation",
        )

    monkeypatch.setattr("backend.ai.chat.default_registry.execute", execute)
    text, grounded, _ = _generate_reply(
        None,
        [_symbol_block("AAPL")],
        [],
        None,
        [],
        "which indicators triggered the AAPL signal?",
        None,
        False,
        ["AAPL"],
        {},
    )

    assert grounded is True
    assert "signal_explanation" in text
    assert requests[0].arguments["symbol"] == "AAPL"
    complete.assert_not_called()


def test_counterargument_and_sensitivity_route_to_typed_tools(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    requests = []

    def execute(request):
        requests.append(request)
        return ToolResult(tool_name=request.tool_name, ok=True, data={"status": "verified"}, provider="MarketLens")

    monkeypatch.setattr("backend.ai.chat.default_registry.execute", execute)
    text, grounded, _ = _generate_reply(
        None,
        [_symbol_block("AAPL")],
        [],
        None,
        [],
        "what would invalidate the AAPL signal?",
        None,
        False,
        ["AAPL"],
        {},
    )
    assert grounded is True
    assert "counterargument_review" in text
    assert requests[-1].tool_name == "counterargument_review"

    text, grounded, _ = _generate_reply(
        None,
        [],
        [],
        None,
        [],
        "show sensitivity for my entry versus stop",
        None,
        False,
        [],
        {},
    )
    assert grounded is True
    assert "sensitivity_analysis" in text
    assert requests[-1].tool_name == "sensitivity_analysis"
    complete.assert_not_called()


def test_event_timeline_routes_to_typed_tool(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    requests = []
    monkeypatch.setattr(
        "backend.ai.chat.default_registry.execute",
        lambda request: (requests.append(request) or ToolResult(tool_name=request.tool_name, ok=True, data={"events": []}, provider="MarketLens timeline")),
    )

    text, grounded, _ = _generate_reply(
        None,
        [_symbol_block("AAPL")],
        [],
        None,
        [],
        "what happened after earnings for AAPL?",
        None,
        False,
        ["AAPL"],
        {},
    )

    assert grounded is True
    assert "market_event_timeline" in text
    assert requests[0].tool_name == "market_event_timeline"
    complete.assert_not_called()
