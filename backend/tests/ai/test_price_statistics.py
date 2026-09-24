"""BF-04: return, volatility, max drawdown and correlation from daily closes.

Covers the calculator's return correlation, the get_price_statistics tool
(bars patched, "today" pinned), the phrase parser, Chat routing, and one
turn end to end through answer_verifier.
"""

import math
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from backend.ai.calculator import CalculationRequest, calculate
from backend.ai.market_tools import (
    PriceStatisticsRequest,
    _daily_range_covering,
    _Payload,
    get_price_statistics_tool,
)
from backend.ai.price_metric_intent import PriceMetricIntent, parse_price_metric_intent
from backend.tests.ai.test_chat import WARM_CTX, _Base

TODAY = date(2026, 9, 24)


def _trading_days(start: date, count: int) -> list[date]:
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _bars(closes_by_day: dict[date, float]) -> list[dict]:
    return [{"timestamp": f"{day.isoformat()}T00:00:00", "close": close} for day, close in sorted(closes_by_day.items())]


@pytest.fixture
def pinned_today(monkeypatch):
    monkeypatch.setattr("backend.utils.timezone.now_ny", lambda: datetime(2026, 9, 24, 12, 0))


@pytest.fixture
def bars_by_symbol(monkeypatch):
    series: dict[str, dict[date, float]] = {}
    requests: list = []

    def fake_bars(request):
        requests.append(request)
        closes = series[request.symbol]
        return _Payload(
            symbol=request.symbol,
            bars=_bars(closes),
            provider="test",
            source_timestamp=f"{max(closes).isoformat()}T16:00:00-04:00",
            session=request.session,
        )

    monkeypatch.setattr("backend.ai.market_tools.get_bars_tool", fake_bars)
    return series, requests


# --- calculator ------------------------------------------------------------


def test_return_correlation_correlates_returns_not_price_levels() -> None:
    # Both series trend up, so their price LEVELS correlate strongly, but
    # their day-to-day moves are exact opposites.
    left = [100, 102, 103, 105, 106, 108]
    right = [100, 101, 103, 104, 106, 107]
    returns = calculate(CalculationRequest(calculation="return_correlation", prices=left, comparison_prices=right))
    levels = calculate(CalculationRequest(calculation="correlation", prices=left, comparison_prices=right))

    assert levels.values["correlation"] > 0.9
    assert returns.values["correlation"] < -0.9
    assert returns.values["return_observations"] == 5
    assert any("price levels" in item for item in returns.assumptions)


def test_return_correlation_rejects_short_or_flat_series() -> None:
    with pytest.raises(ValueError):
        calculate(CalculationRequest(calculation="return_correlation", prices=[1, 2], comparison_prices=[1, 2]))
    with pytest.raises(ValueError):
        calculate(
            CalculationRequest(calculation="return_correlation", prices=[1, 2, 3], comparison_prices=[5, 5, 5])
        )


# --- tool ------------------------------------------------------------------


def test_return_between_dates_uses_the_closes_inside_the_window(pinned_today, bars_by_symbol) -> None:
    series, _ = bars_by_symbol
    days = _trading_days(date(2026, 1, 2), 20)
    series["NVDA"] = {day: 100 + index for index, day in enumerate(days)}

    result = get_price_statistics_tool(
        PriceStatisticsRequest(symbol="NVDA", metric="return_percent", start=date(2026, 1, 5), end=date(2026, 1, 20))
    )

    assert (result.start_date, result.end_date) == ("2026-01-05", "2026-01-20")
    start_close = series["NVDA"][date(2026, 1, 5)]
    end_close = series["NVDA"][date(2026, 1, 20)]
    assert result.values["start_close"] == start_close
    assert result.values["end_close"] == end_close
    assert result.values["return_percent"] == pytest.approx((end_close - start_close) / start_close * 100)
    assert result.unknowns == []


def test_volatility_reports_daily_and_annualized(pinned_today, bars_by_symbol) -> None:
    series, requests = bars_by_symbol
    days = _trading_days(date(2026, 8, 20), 25)
    series["AAPL"] = {day: 100 * (1.01 if index % 2 else 0.99) ** index for index, day in enumerate(days)}

    result = get_price_statistics_tool(PriceStatisticsRequest(symbol="AAPL", metric="volatility", lookback_days=30))

    # Noon on TODAY is mid-session, so the window ends at the previous close.
    last_close = TODAY - timedelta(days=1)
    closes = [
        close for day, close in sorted(series["AAPL"].items())
        if last_close - timedelta(days=30) <= day <= last_close
    ]
    expected_daily = calculate(CalculationRequest(calculation="volatility", prices=closes)).values["period_volatility"]
    assert result.values["daily_volatility_percent"] == pytest.approx(expected_daily)
    assert result.values["annualized_volatility_percent"] == pytest.approx(expected_daily * math.sqrt(252))
    assert result.start_date >= (last_close - timedelta(days=30)).isoformat()
    assert result.end_date == last_close.isoformat()
    # 30 days plus the edge slack needs more than the 1mo range covers.
    assert requests[0].range == "3mo" and requests[0].timeframe == "1d"


def test_max_drawdown_names_the_peak_and_trough(pinned_today, bars_by_symbol) -> None:
    series, _ = bars_by_symbol
    days = _trading_days(date(2026, 1, 5), 6)
    series["TSLA"] = dict(zip(days, [100, 120, 90, 110, 80, 130], strict=True))

    result = get_price_statistics_tool(
        PriceStatisticsRequest(symbol="TSLA", metric="max_drawdown", start=date(2026, 1, 1), end=TODAY)
    )

    assert result.values["max_drawdown_percent"] == pytest.approx((120 - 80) / 120 * 100)
    assert (result.values["peak_close"], result.values["peak_date"]) == (120, days[1].isoformat())
    assert (result.values["trough_close"], result.values["trough_date"]) == (80, days[4].isoformat())
    # The window ran to today but the data stops in January: say so.
    assert any("latest daily close" in item["reason"] for item in result.unknowns)


def test_correlation_uses_only_shared_trading_days(pinned_today, bars_by_symbol) -> None:
    series, _ = bars_by_symbol
    days = _trading_days(date(2026, 9, 1), 12)
    series["AAPL"] = {day: 100 + index + (index % 3) for index, day in enumerate(days)}
    series["MSFT"] = {day: 200 + 2 * index - (index % 2) for index, day in enumerate(days) if day != days[5]}

    result = get_price_statistics_tool(
        PriceStatisticsRequest(symbol="AAPL", metric="correlation", comparison_symbol="MSFT", lookback_days=60)
    )

    shared = [day for day in days if day != days[5]]
    expected = calculate(
        CalculationRequest(
            calculation="return_correlation",
            prices=[series["AAPL"][day] for day in shared],
            comparison_prices=[series["MSFT"][day] for day in shared],
        )
    ).values["correlation"]
    assert result.values["correlation"] == pytest.approx(expected)
    assert result.observations == len(shared)
    assert result.comparison_symbol == "MSFT"
    assert result.provider == "test"


def test_history_starting_after_the_request_is_reported(pinned_today, bars_by_symbol) -> None:
    series, _ = bars_by_symbol
    series["NEWCO"] = {day: 10 + index for index, day in enumerate(_trading_days(date(2026, 6, 1), 40))}

    result = get_price_statistics_tool(
        PriceStatisticsRequest(symbol="NEWCO", metric="max_drawdown", start=date(2026, 1, 1), end=date(2026, 7, 15))
    )

    assert result.start_date == "2026-06-01"
    assert any("history available from 2026-06-01" in item["reason"] for item in result.unknowns)


def test_too_few_closes_is_an_error_not_a_number(pinned_today, bars_by_symbol) -> None:
    series, _ = bars_by_symbol
    series["AAPL"] = {date(2026, 9, 21): 100.0, date(2026, 9, 22): 101.0}

    with pytest.raises(ValueError, match="fewer than 3 daily closes"):
        get_price_statistics_tool(PriceStatisticsRequest(symbol="AAPL", metric="volatility", lookback_days=10))


def test_request_validation() -> None:
    with pytest.raises(ValidationError):
        PriceStatisticsRequest(symbol="AAPL", metric="correlation")
    with pytest.raises(ValidationError):
        PriceStatisticsRequest(symbol="AAPL", metric="correlation", comparison_symbol="aapl")
    with pytest.raises(ValidationError):
        PriceStatisticsRequest(symbol="AAPL", metric="return_percent")
    with pytest.raises(ValidationError):
        PriceStatisticsRequest(symbol="AAPL", metric="volatility", start=date(2026, 2, 1), end=date(2026, 1, 1))
    with pytest.raises(ValidationError):
        PriceStatisticsRequest(symbol="AAPL", metric="volatility", start=date(2026, 1, 1), lookback_days=30)


def test_daily_range_is_the_smallest_supported_one_covering_the_window() -> None:
    assert _daily_range_covering(TODAY - timedelta(days=20), TODAY) == "1mo"
    assert _daily_range_covering(TODAY - timedelta(days=200), TODAY) == "1y"
    assert _daily_range_covering(date(2024, 1, 1), TODAY) == "5y"
    with pytest.raises(ValueError):
        _daily_range_covering(date(2019, 1, 1), TODAY)


# --- phrase parser -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "symbols", "expected"),
    [
        ("What is TSLA's max drawdown this year?", ["TSLA"],
         PriceMetricIntent("max_drawdown", "TSLA", None, date(2026, 1, 1), TODAY, None)),
        ("What's AAPL volatility over 30 days?", ["AAPL"],
         PriceMetricIntent("volatility", "AAPL", None, None, None, 30)),
        ("AAPL volatility", ["AAPL"],
         PriceMetricIntent("volatility", "AAPL", None, None, None, None)),
        ("What's the correlation between AAPL and MSFT?", ["AAPL", "MSFT"],
         PriceMetricIntent("correlation", "AAPL", "MSFT", None, None, None)),
        ("How correlated is NVDA with the market?", ["NVDA"],
         PriceMetricIntent("correlation", "NVDA", "SPY", None, None, None)),
        ("What was NVDA's return from Jan 5 to Jan 20?", ["NVDA"],
         PriceMetricIntent("return_percent", "NVDA", None, date(2026, 1, 5), date(2026, 1, 20), None)),
        ("What's the percent change for AAPL from 2024 to 2025?", ["AAPL"],
         PriceMetricIntent("return_percent", "AAPL", None, date(2024, 1, 1), date(2025, 12, 31), None)),
        ("How did TSLA perform last year?", ["TSLA"],
         PriceMetricIntent("return_percent", "TSLA", None, date(2025, 1, 1), date(2025, 12, 31), None)),
        ("AAPL return over the last year", ["AAPL"],
         PriceMetricIntent("return_percent", "AAPL", None, None, None, 365)),
        ("NVDA return since Mar 3", ["NVDA"],
         PriceMetricIntent("return_percent", "NVDA", None, date(2026, 3, 3), TODAY, None)),
        # A date later in the year than today means last year's.
        ("MSFT return from Oct 5 to Oct 20", ["MSFT"],
         PriceMetricIntent("return_percent", "MSFT", None, date(2025, 10, 5), date(2025, 10, 20), None)),
        ("TSLA drawdown in 2024", ["TSLA"],
         PriceMetricIntent("max_drawdown", "TSLA", None, date(2024, 1, 1), date(2024, 12, 31), None)),
    ],
)
def test_parser_reads_metric_symbols_and_window(text, symbols, expected) -> None:
    assert parse_price_metric_intent(text, symbols, TODAY) == expected


@pytest.mark.parametrize(
    ("text", "symbols"),
    [
        ("What's the drawdown from 200 to 150 for AAPL?", ["AAPL"]),  # the trader's own prices
        ("What's the drawdown from $200 to $150.50?", ["AAPL"]),
        ("What's AAPL implied volatility?", ["AAPL"]),  # an options question
        ("Why is AAPL down this year?", ["AAPL"]),
        ("What's AAPL's return?", ["AAPL"]),  # no window: today's change
        ("Compare AAPL and MSFT volatility", ["AAPL", "MSFT"]),  # a comparison
        ("What's the max drawdown?", []),  # no ticker: calculator or clarification
    ],
)
def test_parser_steps_aside(text, symbols) -> None:
    assert parse_price_metric_intent(text, symbols, TODAY) is None


@pytest.mark.parametrize(
    ("text", "symbols", "fragment"),
    [
        ("correlation of AAPL", ["AAPL"], "correlate AAPL with"),
        ("correlation of AAPL, MSFT and NVDA", ["AAPL", "MSFT", "NVDA"], "which two"),
        ("TSLA drawdown in 2027", ["TSLA"], "hasn't happened yet"),
        ("AAPL return from Mar 30 to Feb 31", ["AAPL"], "couldn't read"),
    ],
)
def test_parser_asks_instead_of_guessing(text, symbols, fragment) -> None:
    reply = parse_price_metric_intent(text, symbols, TODAY)
    assert isinstance(reply, str) and fragment in reply


# --- Chat routing --------------------------------------------------------------


def _route(text, symbols, planner_state=None):
    from backend.ai.chat_routing import _build_deterministic_chat_reply

    with patch("backend.ai.chat_routing.now_ny", lambda: datetime(2026, 9, 24, 12, 0)):
        return _build_deterministic_chat_reply(text, focus_symbols=symbols, planner_state=planner_state or {})


def test_chat_routes_metric_questions_to_the_tool() -> None:
    parsed = _route("What is TSLA's max drawdown this year?", ["TSLA"])
    assert parsed.action == "get_price_statistics"
    assert parsed.action_tool_arguments == {
        "symbol": "TSLA", "metric": "max_drawdown", "start": "2026-01-01", "end": "2026-09-24",
    }
    assert _route("What's the correlation between AAPL and MSFT?", ["AAPL", "MSFT"]).action == "get_price_statistics"
    # BF-03's example now gets a real answer instead of the calculator.
    parsed = _route("What was NVDA's return from Jan 5 to Jan 20?", ["NVDA"])
    assert parsed.action == "get_price_statistics"
    assert parsed.action_tool_arguments["start"] == "2026-01-05"


def test_chat_keeps_the_existing_route_where_it_answers_trailing_returns() -> None:
    # The semantic route already answers "over the last week" with a
    # verified change_percent; the new tool does not take it over.
    parsed = _route("AAPL performance over the last week", ["AAPL"])
    assert (parsed.action, parsed.action_tool_arguments["indicator"]) == ("get_indicator", "change_percent")
    # "over the last 5 days" had no route at all (it asked for calculator
    # values); the new tool answers it.
    parsed = _route("How has AAPL performed over the last 5 days?", ["AAPL"])
    assert (parsed.action, parsed.action_tool_arguments["lookback_days"]) == ("get_price_statistics", 5)


def test_a_bare_it_no_longer_reruns_the_last_calculation() -> None:
    """BF-04: "what's the return on it" re-ran the remembered position_risk."""
    state = {"last_calculation_inputs": {"calculation": "position_risk", "shares": 100, "entry_price": 50, "stop_price": 48}}

    reply = _route("what's the return on it", [], state)

    assert isinstance(reply, str) and "What values should I use" in reply
    parsed = _route("same inputs again please, what's the return", [], state)
    assert parsed.action == "calculate"


# --- one turn end to end -------------------------------------------------------


class TestPriceStatisticsTurn(_Base):
    @patch("backend.ai.chat_model.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_drawdown_question_is_answered_and_verified(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["TSLA"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.complete = AsyncMock()
        days = _trading_days(date(2026, 1, 5), 6)
        closes = dict(zip(days, [100, 120, 90, 110, 80, 130], strict=True))

        def fake_bars(request):
            return _Payload(
                symbol=request.symbol, bars=_bars(closes), provider="test",
                source_timestamp=f"{days[-1].isoformat()}T16:00:00-04:00", session=request.session,
            )

        from backend.ai.chat import answer_chat_message

        with (
            patch("backend.ai.market_tools.get_bars_tool", fake_bars),
            patch("backend.utils.timezone.now_ny", lambda: datetime(2026, 9, 24, 12, 0)),
        ):
            msg, grounded, *_ = answer_chat_message(self.session.id, "What is TSLA's max drawdown this year?")

        self.assertIn("TSLA's largest drawdown was 33.33%", msg.content)
        self.assertIn("$120.00 on Jan 6", msg.content)
        mock_ai.complete.assert_not_called()
        verification = next(block for block in msg.response_blocks_payload if block["type"] == "verification")
        self.assertEqual(verification["data"]["status"], "verified", verification)



@pytest.mark.parametrize(("hour", "last_day"), [(12, "2026-09-23"), (17, "2026-09-24")])
def test_todays_bar_counts_only_once_its_session_has_closed(monkeypatch, bars_by_symbol, hour, last_day) -> None:
    """Found live on 2026-09-24: mid-session, the daily feed carries a bar
    for today built from live 1-minute data, and it was counted as a close."""
    monkeypatch.setattr("backend.utils.timezone.now_ny", lambda: datetime(2026, 9, 24, hour, 0))
    series, _ = bars_by_symbol
    series["TSLA"] = dict(zip(_trading_days(date(2026, 9, 21), 4), [100, 90, 95, 80], strict=True))

    result = get_price_statistics_tool(PriceStatisticsRequest(symbol="TSLA", metric="max_drawdown", lookback_days=10))

    assert result.end_date == last_day
    assert result.source_timestamp.startswith(f"{last_day}T16:00:00")
