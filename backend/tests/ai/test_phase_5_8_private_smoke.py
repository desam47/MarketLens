"""Sanctioned Phase 5.8 private-flow smoke tests.

These fixtures are deliberately synthetic.  They exercise the risk and
journal paths without real browser-local data, network providers, or model
calls, while checking that the persisted observability shape never carries
the private journal prose.
"""

import json

from backend.ai.chat import _run_market_tool
from backend.ai.chat_observability import build_turn_observability
from backend.ai.market_tools import (
    JournalCoachRequest,
    PositionInput,
    PortfolioRiskRequest,
    TradeJournalRequest,
    _Payload,
    assess_portfolio_risk_tool,
    get_risk_dashboard_tool,
    trade_journal_coach_tool,
)


def _synthetic_bars(request) -> _Payload:
    closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
    return _Payload(
        symbol=request.symbol,
        provider="synthetic fixture",
        source_timestamp="2026-09-23T10:00:00-04:00",
        bars=[{"close": close, "volume": 1_000} for close in closes],
    )


def _private_entries() -> list[dict]:
    return [{
        "symbol": "AAPL",
        "side": "long",
        "status": "closed",
        "entry_price": 100,
        "exit_price": 110,
        "quantity": 10,
        "planned_stop": 95,
        "planned_target": 115,
        "setup": "breakout",
        "notes": "LOCAL_ONLY journal note 5.8",
        "thesis": "LOCAL_ONLY private thesis 5.8",
    }]


def test_synthetic_private_risk_and_journal_flow_stays_local(monkeypatch) -> None:
    positions = [
        PositionInput(
            symbol="AAPL",
            quantity=10,
            entry_price=100,
            current_price=110,
            stop_price=95,
            sector="Technology",
        ),
        PositionInput(
            symbol="MSFT",
            quantity=5,
            entry_price=200,
            current_price=205,
            stop_price=190,
            sector="Technology",
        ),
    ]
    monkeypatch.setattr("backend.ai.market_tools.get_bars_tool", _synthetic_bars)

    risk = get_risk_dashboard_tool(PortfolioRiskRequest(positions=positions))
    assert risk.available is True
    assert risk.gross_exposure == 2125

    risk_result = assess_portfolio_risk_tool(
        PortfolioRiskRequest(positions=positions, lookback_days=20)
    ).model_dump(mode="json")
    journal_result = trade_journal_coach_tool(
        JournalCoachRequest(entries=_private_entries())
    ).model_dump(mode="json")

    assert risk_result["available"] is True
    assert journal_result["win_rate_percent"] == 100.0
    serialized_results = json.dumps([risk_result, journal_result], sort_keys=True)
    assert "LOCAL_ONLY" not in serialized_results

    trace: list[dict] = []

    class Parsed:
        action = "assess_portfolio_risk"
        action_confirmed = False
        action_tool_arguments = {"positions": [position.model_dump(mode="json") for position in positions]}

    _run_market_tool(None, Parsed(), trace=trace)

    class JournalParsed:
        action = "trade_journal_coach"
        action_confirmed = False
        action_tool_arguments = {"entries": _private_entries()}

    _run_market_tool(None, JournalParsed(), trace=trace)
    serialized_trace = json.dumps(trace, sort_keys=True, default=str)
    assert "LOCAL_ONLY" not in serialized_trace
    assert all(item.get("provider_request_count") == 0 for item in trace)

    observability = build_turn_observability(trace, started_at=0.0)
    assert observability["provider_requests"] == 0
    assert observability["failure_kinds"] == []
