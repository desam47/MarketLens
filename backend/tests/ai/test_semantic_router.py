from backend.ai.semantic_router import route_semantic_intent


def test_watchlist_language_maps_to_canonical_intelligence_concern() -> None:
    cases = {
        "Which of my names look weak?": "weak",
        "show me the laggards in my stocks": "underperforming",
        "what is deteriorating in my watchlist?": "deteriorating",
        "which of my holdings are underperforming?": "underperforming",
        "which of my names are strongest?": "strong",
    }

    for question, concern in cases.items():
        route = route_semantic_intent(question)
        assert route is not None
        assert route.action == "get_watchlist_intelligence"
        assert route.arguments == {"concern": concern}


def test_unscoped_strength_language_does_not_guess_a_watchlist() -> None:
    assert route_semantic_intent("Which names are strongest in the market?") is None


def test_named_watchlist_scope_is_preserved() -> None:
    cases = [
        ("Which of my names look weak in Default watchlist?", "weak", "Default"),
        ("show the laggards in the Market Context watchlist", "underperforming", "Market Context"),
        ('which names are strongest on "Swing" watchlist?', "strong", "Swing"),
    ]

    for question, concern, name in cases:
        route = route_semantic_intent(question)
        assert route is not None
        assert route.action == "get_watchlist_intelligence"
        assert route.arguments == {"concern": concern, "name": name}


def test_watchlist_timeframe_scope_is_preserved() -> None:
    route = route_semantic_intent("Which of my names are weakest on the daily timeframe?")

    assert route is not None
    assert route.arguments == {"concern": "weak", "timeframe": "1d"}


def test_watchlist_timeframe_followup_reuses_named_scope() -> None:
    route = route_semantic_intent(
        "What about the weekly timeframe?",
        planner_state={
            "watchlist_scope": {
                "name": "Default",
                "aggregate": False,
                "concern": "weak",
            }
        },
    )

    assert route is not None
    assert route.action == "get_watchlist_intelligence"
    assert route.arguments == {"concern": "weak", "timeframe": "1wk", "name": "Default"}


def test_watchlist_timeframe_followup_reuses_aggregate_scope() -> None:
    route = route_semantic_intent(
        "What about the weekly timeframe?",
        planner_state={
            "watchlist_scope": {
                "name": "All active watchlists",
                "aggregate": True,
                "concern": "weak",
            }
        },
    )

    assert route is not None
    assert route.arguments == {"concern": "weak", "timeframe": "1wk"}


def test_common_symbol_questions_map_to_existing_typed_tools() -> None:
    cases = {
        "why did AAPL move today?": "why_did_it_move",
        "show AAPL headlines": "get_news",
        "what is AAPL's trend?": "get_trend",
        "How's NVDA looking?": "get_trend",
        "show AAPL options": "get_options_snapshot",
        "What's AAPL doing?": "get_trend",
        "How is AAPL performing?": "get_trend",
        "Tell me about AAPL": "get_trend",
        "Why is AAPL weak?": "get_trend",
        "How did AAPL do today?": "get_trend",
        "What about AAPL?": "get_trend",
    }

    for question, action in cases.items():
        symbols = ["NVDA"] if "NVDA" in question else ["AAPL"]
        route = route_semantic_intent(question, focus_symbols=symbols)
        assert route is not None
        assert route.action == action


def test_common_market_and_indicator_questions_map_to_typed_tools() -> None:
    market = route_semantic_intent("How are stocks doing today?")
    assert market is not None
    assert market.action == "get_market_context"

    indicator = route_semantic_intent("Give me the RSI for AAPL", focus_symbols=["AAPL"])
    assert indicator is not None
    assert indicator.action == "get_indicator"
    assert indicator.arguments == {"symbol": "AAPL", "indicator": "rsi", "timeframe": "1d"}

    risk = route_semantic_intent("Show my positions")
    assert risk is not None
    assert risk.action == "get_risk_dashboard"


def test_market_overview_maps_to_market_context_without_ticker_guessing() -> None:
    route = route_semantic_intent("What's the market doing today?")

    assert route is not None
    assert route.action == "get_market_context"
    assert route.arguments == {}


def test_multi_symbol_comparison_maps_to_typed_tool() -> None:
    route = route_semantic_intent(
        "Compare AAPL and MSFT",
        focus_symbols=["AAPL", "MSFT"],
    )

    assert route is not None
    assert route.action == "compare_symbols"
    assert route.arguments == {
        "symbols": ["AAPL", "MSFT"],
        "metric": "return_percent",
        "direction": "desc",
    }
