"""_visual_trace_payload — maps a tool's result data to a typed visual block.

Covers the ranked_results additions (5.7.1 gap: comparison/ranked blocks
from tool results) for get_relative_strength and anomaly_analysis, plus a
few pre-existing mappings that had no direct unit coverage before this.
"""

from __future__ import annotations

from backend.ai.chat_actions import _visual_trace_payload


def test_relative_strength_ranks_by_rs_pct_descending() -> None:
    data = {
        "symbol": "AAPL",
        "signals": [
            {"benchmark": "SPY", "rs_pct": 1.2, "classification": "outperformer"},
            {"benchmark": "QQQ", "rs_pct": 4.5, "classification": "strong_outperformer"},
            {"benchmark": "XLK", "rs_pct": -0.8, "classification": "underperformer"},
        ],
    }
    result = _visual_trace_payload("get_relative_strength", data)
    assert result is not None
    visual_type, visual_data = result
    assert visual_type == "ranked_results"
    names = [item["name"] for item in visual_data["items"]]
    assert names == ["vs QQQ", "vs SPY", "vs XLK"]
    assert visual_data["items"][0]["score"] == 4.5
    assert visual_data["items"][0]["classification"] == "strong_outperformer"


def test_relative_strength_missing_rs_pct_sorts_last_not_dropped() -> None:
    data = {
        "symbol": "AAPL",
        "signals": [
            {"benchmark": "SPY", "rs_pct": 1.0, "classification": "outperformer"},
            {"benchmark": "QQQ", "classification": "unknown"},  # no rs_pct
        ],
    }
    _, visual_data = _visual_trace_payload("get_relative_strength", data)
    assert len(visual_data["items"]) == 2
    assert visual_data["items"][-1]["name"] == "vs QQQ"


def test_relative_strength_empty_signals_returns_empty_ranked_block() -> None:
    # An empty list is still a list — "we checked, found nothing" renders as
    # an empty ranked block, consistent with anomaly_analysis below, rather
    # than silently vanishing.
    visual_type, visual_data = _visual_trace_payload(
        "get_relative_strength", {"symbol": "AAPL", "signals": []}
    )
    assert visual_type == "ranked_results"
    assert visual_data["items"] == []


def test_relative_strength_missing_field_returns_none() -> None:
    assert _visual_trace_payload("get_relative_strength", {"symbol": "AAPL"}) is None


def test_anomaly_analysis_ranks_by_absolute_z_score() -> None:
    data = {
        "symbol": "TSLA",
        "anomalies": [
            {"type": "price_return", "severity": "elevated", "value": 3.1, "z_score": 2.4},
            {"type": "volume", "severity": "high", "value": 900000, "z_score": -5.1},
            {"type": "large_prints", "severity": "elevated", "value": [1, 2]},  # no z_score
        ],
    }
    visual_type, visual_data = _visual_trace_payload("anomaly_analysis", data)
    assert visual_type == "ranked_results"
    names = [item["name"] for item in visual_data["items"]]
    # |−5.1| > |2.4| > (no z_score sorts last)
    assert names == ["volume", "price return", "large prints"]
    assert visual_data["items"][0]["score"] == -5.1
    assert visual_data["items"][-1]["score"] is None


def test_watchlist_intelligence_builds_ranked_results_block() -> None:
    visual_type, visual_data = _visual_trace_payload(
        "get_watchlist_intelligence",
        {
            "concern": "weak",
            "watchlist_name": "Core",
            "data_status": "ready",
            "watchlist_size": 2,
            "analyzed_symbols": 2,
            "top_bearish": [{"symbol": "AAPL", "score": -18, "change_pct": -3.2}],
            "deteriorating": [{"symbol": "MSFT", "score": -11}],
        },
    )

    assert visual_type == "ranked_results"
    assert visual_data["title"] == "Weak watchlist names"
    assert [item["name"] for item in visual_data["items"]] == ["AAPL", "MSFT"]
    assert visual_data["coverage"]["analyzed_symbols"] == 2


def test_anomaly_analysis_no_anomalies_still_returns_empty_ranked_block() -> None:
    visual_type, visual_data = _visual_trace_payload(
        "anomaly_analysis", {"symbol": "TSLA", "anomalies": []}
    )
    assert visual_type == "ranked_results"
    assert visual_data["items"] == []


def test_anomaly_analysis_missing_field_returns_none() -> None:
    assert _visual_trace_payload("anomaly_analysis", {"symbol": "TSLA"}) is None


def test_unrelated_action_returns_none() -> None:
    assert _visual_trace_payload("get_news", {"symbol": "AAPL", "articles": []}) is None


def test_compare_symbols_still_produces_comparison_table() -> None:
    data = {"rankings": [{"rank": 1, "symbol": "AAPL", "value": 10.0, "metric": "rsi"}]}
    visual_type, visual_data = _visual_trace_payload("compare_symbols", data)
    assert visual_type == "comparison_table"
    assert visual_data["rows"] == [[1, "AAPL", 10.0, "rsi"]]


def test_bars_become_a_bounded_chart() -> None:
    kind, payload = _visual_trace_payload("get_bars", {"symbol": "AAPL", "timeframe": "5m", "session": "regular", "bars": [{"close": i} for i in range(200)]})
    assert kind == "chart"
    assert len(payload["bars"]) == 120  # bounded, newest kept
    assert payload["bars"][-1] == {"close": 199}


def test_indicator_becomes_an_indicator_table() -> None:
    kind, payload = _visual_trace_payload("get_indicator", {"symbol": "AAPL", "indicator": "rsi", "value": 41.5})
    assert kind == "indicator_table"
    assert payload["indicators"] == {"rsi": 41.5}


def test_risk_dashboard_becomes_a_risk_card_without_positions() -> None:
    kind, payload = _visual_trace_payload("get_risk_dashboard", {"portfolio_value": 100_000, "gross_exposure": 60_000, "positions": [{"symbol": "AAPL"}]})
    assert kind == "risk_card"
    assert payload == {"portfolio_value": 100_000, "gross_exposure": 60_000}


def test_scenario_keeps_only_scenario_fields() -> None:
    kind, payload = _visual_trace_payload("scenario_analysis", {"shock_percent": -5, "total_pnl_delta": -3000, "raw_debug": "x"})
    assert kind == "scenario"
    assert payload == {"shock_percent": -5, "total_pnl_delta": -3000}


def test_session_stats_become_a_session_card() -> None:
    kind, payload = _visual_trace_payload("get_session_stats", {"symbol": "AAPL", "session": "premarket", "open": 148.0, "close": 149.5, "bars": [1, 2]})
    assert kind == "session_stats"
    assert payload == {"symbol": "AAPL", "session": "premarket", "open": 148.0, "close": 149.5}


def test_historical_similarity_becomes_historical_outcomes() -> None:
    kind, payload = _visual_trace_payload("historical_similarity", {"symbol": "AAPL", "sample_size": 12, "look_ahead_safe": True, "matches": [1, 2, 3]})
    assert kind == "historical_outcomes"
    assert payload == {"symbol": "AAPL", "sample_size": 12, "look_ahead_safe": True}
