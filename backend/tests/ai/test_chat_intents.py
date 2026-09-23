from types import SimpleNamespace
from unittest.mock import Mock

from backend.ai.answer_verifier import assign_evidence_ids, verify_answer
from backend.ai.chat import (
    _append_context_evidence,
    _generate_reply,
    _generate_reply_streaming,
    _run_market_tool,
)
from backend.ai.prompt import ChatReplyResponse
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


def test_market_overview_routes_to_verified_context_without_ai(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    requests = []

    def execute(request):
        requests.append(request)
        return ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data={
                "regime": "risk_on",
                "confidence": 0.82,
                "trend_strength": 0.61,
                "momentum": 0.24,
                "volatility_state": "normal",
                "timestamp": "2026-09-23T15:00:00-04:00",
            },
            provider="MarketLens engine",
            source_timestamp="2026-09-23T15:00:00-04:00",
            freshness_seconds=60.0,
        )

    monkeypatch.setattr("backend.ai.chat.default_registry.execute", execute)
    text, grounded, _ = _generate_reply(
        None,
        [],
        [],
        None,
        [],
        "What's the market doing today?",
        None,
        False,
        [],
        {},
    )

    assert grounded is True
    assert "risk on" in text
    assert "momentum +0.24" in text
    assert requests[0].tool_name == "get_market_context"
    complete.assert_not_called()


def test_symbol_overview_routes_to_verified_trend_without_ai(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    requests = []

    def execute(request):
        requests.append(request)
        return ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data={
                "symbol": "NVDA",
                "direction": "sideways",
                "strength": "weak",
                "classification": "weak_bullish",
                "data_status": "ok",
            },
            provider="webull",
            source_timestamp="2026-09-23T16:00:00-04:00",
            freshness_seconds=60.0,
        )

    monkeypatch.setattr("backend.ai.chat.default_registry.execute", execute)
    text, grounded, _ = _generate_reply(
        None,
        [_symbol_block("NVDA")],
        [],
        None,
        [],
        "How's NVDA looking?",
        None,
        False,
        ["NVDA"],
        {},
    )

    assert grounded is True
    assert "NVDA trend" in text
    assert "weak bullish classification" in text
    assert requests[0].tool_name == "get_trend"
    assert requests[0].timeframe == "1d"
    complete.assert_not_called()


def test_streaming_symbol_overview_uses_the_same_typed_route(monkeypatch) -> None:
    requests = []

    def execute(request):
        requests.append(request)
        return ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data={"symbol": "NVDA", "direction": "sideways", "strength": "weak", "classification": "weak_bullish"},
            provider="webull",
            source_timestamp="2026-09-23T16:00:00-04:00",
            freshness_seconds=60.0,
        )

    monkeypatch.setattr("backend.ai.chat.default_registry.execute", execute)
    turn = SimpleNamespace(
        symbol_blocks=[_symbol_block("NVDA")],
        unavailable=[],
        base=["NVDA"],
        user_content="How's NVDA looking?",
        planner_state={},
        market_baseline=None,
        transcript=[],
        alert_context=None,
        preferences=None,
        chart_state=None,
    )
    events = list(_generate_reply_streaming(None, turn, []))

    result = next(payload for kind, payload in events if kind == "result")
    assert result[1] is True
    assert "NVDA trend" in result[0]
    assert requests[0].tool_name == "get_trend"


def test_prompt_context_is_available_to_the_answer_verifier() -> None:
    trace = []
    turn = SimpleNamespace(
        symbol_blocks=[
            {
                "symbol": "AAPL",
                "context": {
                    "price": 101.0,
                    "direction": "bullish",
                    "source_timestamp": "2026-09-23T16:00:00-04:00",
                },
            }
        ],
        market_baseline={"regime_live": {"regime": "risk_on", "source_timestamp": "2026-09-23T16:00:00-04:00"}},
    )
    _append_context_evidence(trace, turn)
    assign_evidence_ids(trace)

    result = verify_answer("AAPL is bullish at $101.", trace, allowed_symbols=["AAPL"])

    assert result.status == "verified"
    assert result.evidence_refs == ["ev-1", "ev-2"]


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


def test_watchlist_semantics_route_without_model_guessing(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    requests = []

    def execute(request):
        requests.append(request)
        return ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data={
                "watchlist_name": "Core",
                "concern": "weak",
                "data_status": "ready",
                "watchlist_size": 3,
                "analyzed_symbols": 3,
                "top_bearish": [{"symbol": "AAPL", "change_pct": -3.2, "score": -18}],
                "deteriorating": [],
                "relative_strength": [],
            },
            provider="MarketLens scanner cache",
            source_timestamp="2026-09-23T14:00:00-04:00",
        )

    monkeypatch.setattr("backend.ai.chat.default_registry.execute", execute)
    text, grounded, _ = _generate_reply(
        None,
        [],
        [],
        None,
        [],
        "Which of my names look weak?",
        None,
        False,
        [],
        {},
    )

    assert grounded is True
    assert "AAPL" in text
    assert requests[0].tool_name == "get_watchlist_intelligence"
    assert requests[0].arguments["concern"] == "weak"
    complete.assert_not_called()


def test_named_watchlist_semantics_preserves_scope(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    requests = []

    def execute(request):
        requests.append(request)
        return ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data={
                "watchlist_name": "Default",
                "concern": "weak",
                "data_status": "ready",
                "watchlist_size": 1,
                "analyzed_symbols": 1,
                "top_bearish": [{"symbol": "AAPL", "change_pct": -3.2, "score": -18}],
                "deteriorating": [],
            },
            provider="MarketLens scanner cache",
            source_timestamp="2026-09-23T14:00:00-04:00",
        )

    monkeypatch.setattr("backend.ai.chat.default_registry.execute", execute)
    text, grounded, _ = _generate_reply(
        None,
        [],
        [],
        None,
        [],
        "Which of my names look weak in Default watchlist?",
        None,
        False,
        [],
        {},
    )

    assert grounded is True
    assert "Default" in text
    assert requests[0].tool_name == "get_watchlist_intelligence"
    assert requests[0].arguments == {"concern": "weak", "name": "Default"}
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
            data={
                "rankings": [
                    {"symbol": "AAPL", "rank": 1, "value": 12.5, "metric": "volatility_percent"},
                    {"symbol": "MSFT", "rank": 2, "value": 4.2, "metric": "volatility_percent"},
                ],
                "unknowns": [],
            },
            provider="MarketLens comparison",
        )

    monkeypatch.setattr("backend.ai.chat.default_registry.execute", execute)
    trace = []
    text, grounded, _ = _generate_reply(
        None,
        [_symbol_block("AAPL"), _symbol_block("MSFT")],
        [],
        None,
        [],
        "compare AAPL and MSFT by volatility",
        None,
        False,
        ["AAPL", "MSFT"],
        {},
        trace,
    )

    assert grounded is True
    assert "compare_symbols" in text
    assert requests[0].arguments["metric"] == "volatility_percent"
    verification = verify_answer(
        text,
        trace,
        allowed_symbols=["AAPL", "MSFT"],
        user_content="compare AAPL and MSFT by volatility",
    )
    assert verification.status == "verified"
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


def test_anomaly_question_routes_to_typed_tool(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    requests = []
    monkeypatch.setattr(
        "backend.ai.chat.default_registry.execute",
        lambda request: (requests.append(request) or ToolResult(tool_name=request.tool_name, ok=True, data={"anomalies": []}, provider="MarketLens anomaly analysis")),
    )

    text, grounded, _ = _generate_reply(
        None,
        [_symbol_block("AAPL")],
        [],
        None,
        [],
        "are there unusual volume anomalies in AAPL?",
        None,
        False,
        ["AAPL"],
        {},
    )

    assert grounded is True
    assert "anomaly_analysis" in text
    assert requests[0].tool_name == "anomaly_analysis"
    complete.assert_not_called()


def test_assumption_save_routes_to_typed_tool_and_requires_explicit_save(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    requests = []
    monkeypatch.setattr(
        "backend.ai.chat.default_registry.execute",
        lambda request: (requests.append(request) or ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data={"assumptions": [{"id": "assumption-0001", "status": "active"}]},
            provider="MarketLens assumption ledger",
        )),
    )

    text, grounded, _ = _generate_reply(
        None,
        [_symbol_block("AAPL")],
        [],
        None,
        [],
        "remember my AAPL growth assumption is 10% and stop at $210",
        None,
        False,
        ["AAPL"],
        {},
    )

    assert grounded is True
    assert "assumption_tracking" in text
    assert requests[0].tool_name == "assumption_tracking"
    assert requests[0].confirmed is True
    assert requests[0].arguments["operation"] == "save"
    assert len(requests[0].arguments["assumptions"]) == 2
    complete.assert_not_called()


def test_assumption_review_routes_without_writing(monkeypatch) -> None:
    complete = Mock()
    monkeypatch.setattr("backend.ai.chat.ai_manager.complete", complete)
    requests = []
    monkeypatch.setattr(
        "backend.ai.chat.default_registry.execute",
        lambda request: (requests.append(request) or ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data={"assumptions": [], "changed": []},
            provider="MarketLens assumption ledger",
        )),
    )

    text, grounded, _ = _generate_reply(
        None,
        [_symbol_block("MSFT")],
        [],
        None,
        [],
        "review my MSFT research assumptions",
        None,
        False,
        ["MSFT"],
        {},
    )

    assert grounded is True
    assert requests[0].tool_name == "assumption_tracking"
    assert requests[0].confirmed is False
    assert requests[0].arguments["operation"] == "review"
    complete.assert_not_called()


def test_assumption_tool_updates_structured_planner_state() -> None:
    planner_state = {}
    parsed = ChatReplyResponse(
        reply="save",
        action="assumption_tracking",
        action_confirmed=True,
        action_tool_arguments={
            "operation": "save",
            "symbol": "AAPL",
            "assumptions": [
                {
                    "category": "invalidation",
                    "statement": "Break below support invalidates the thesis",
                    "source": "user",
                }
            ],
        },
    )
    text, grounded = _run_market_tool(None, parsed, planner_state=planner_state)
    assert grounded is True
    assert "assumption_tracking" in text
    assert planner_state["research_assumptions"][0]["original_statement"].startswith("Break below")
