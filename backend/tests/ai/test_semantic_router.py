from backend.ai.semantic_router import route_semantic_intent


def test_watchlist_language_maps_to_canonical_intelligence_concern() -> None:
    cases = {
        "Which of my names look weak?": "weak",
        "show me the laggards in my stocks": "underperforming",
        "what is deteriorating in my watchlist?": "deteriorating",
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


def test_position_and_holding_language_uses_private_portfolio_scope() -> None:
    cases = [
        "Which of my holdings are weakest on the daily timeframe?",
        "Which of my holdings are underperforming?",
        "Which position has the most downside risk?",
    ]

    for question in cases:
        route = route_semantic_intent(question)
        assert route is not None
        assert route.action == "get_risk_dashboard"
        assert route.arguments == {}


def test_holding_weakness_is_not_reported_as_aggregate_portfolio_risk() -> None:
    route = route_semantic_intent("Which of my holdings are weakest on the daily timeframe?")

    assert route is not None
    assert route.action_query == "portfolio_weakness"


def test_portfolio_change_does_not_fall_back_to_ticker_clarification() -> None:
    route = route_semantic_intent("What changed in my portfolio since yesterday?")

    assert route is not None
    assert route.action == "get_risk_dashboard"
    assert route.action_query == "portfolio_change"


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


def test_daily_change_question_uses_previous_close_comparison() -> None:
    route = route_semantic_intent("what was dvlt change today", focus_symbols=["DVLT"])

    assert route is not None
    assert route.action == "what_changed"
    assert route.arguments == {"symbol": "DVLT", "reference": "previous_close"}


def test_daily_performance_variants_use_previous_close_comparison() -> None:
    for question, symbol in (
        ("how did DVLT move today", "DVLT"),
        ("how much is DVLT down today", "DVLT"),
        ("DVLT performance today", "DVLT"),
        ("How did AAPL perform today?", "AAPL"),
    ):
        route = route_semantic_intent(question, focus_symbols=[symbol])
        assert route is not None
        assert route.action == "what_changed"
        assert route.arguments["reference"] == "previous_close"


def test_lookback_returns_and_explicit_indicator_periods_are_preserved() -> None:
    weekly = route_semantic_intent("What is AAPL weekly return?", focus_symbols=["AAPL"])
    monthly = route_semantic_intent("How has AAPL done over the last month?", focus_symbols=["AAPL"])
    sma = route_semantic_intent("What is AAPL 20-day SMA?", focus_symbols=["AAPL"])

    assert weekly is not None and weekly.action == "get_indicator"
    assert weekly.action_query == "weekly_return"
    assert weekly.arguments["period"] == 5
    assert monthly is not None and monthly.arguments["period"] == 21
    assert sma is not None and sma.arguments["period"] == 20


def test_relative_watchlist_question_preserves_benchmark_scope() -> None:
    route = route_semantic_intent("Which names are weak relative to QQQ?")

    assert route is not None
    assert route.action == "get_watchlist_intelligence"
    assert route.action_query == "benchmark_relative"
    assert route.arguments == {"concern": "underperforming", "benchmark_symbol": "QQQ"}

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
