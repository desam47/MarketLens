"""Daily-bar evidence is judged by trading date, not age.

Found in the running app on 2026-09-24: "TSLA max drawdown this year"
(yesterday's close, the newest complete daily close) showed an Evidence
badge of STALE and "124095s old", because the age was measured from the
daily bar's midnight timestamp against a flat 15-minute limit.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from backend.ai.chat_actions import _run_market_tool
from backend.ai.prompt import ChatReplyResponse
from backend.ai.response_blocks import _item_quality
from backend.ai.tool_registry import ToolResult, _freshness_seconds
from backend.engines.market_calendar import (
    daily_bar_reference_time,
    daily_data_is_current,
    us_market_calendar,
)

ET = ZoneInfo("America/New_York")


def _et(*args) -> datetime:
    return datetime(*args, tzinfo=ET)


# --- the calendar rule ------------------------------------------------------------


def test_latest_completed_session_date() -> None:
    latest = us_market_calendar.latest_completed_session_date
    assert latest(_et(2026, 9, 24, 10, 28)) == date(2026, 9, 23)  # Thursday, in session
    assert latest(_et(2026, 9, 24, 16, 0)) == date(2026, 9, 24)  # at the close
    assert latest(_et(2026, 9, 26, 12, 0)) == date(2026, 9, 25)  # Saturday
    assert latest(_et(2026, 9, 28, 9, 0)) == date(2026, 9, 25)  # Monday, before the open
    assert latest(_et(2026, 11, 27, 11, 0)) == date(2026, 11, 25)  # the day after Thanksgiving


def test_daily_data_is_current_by_date() -> None:
    now = _et(2026, 9, 24, 10, 28)
    assert daily_data_is_current("2026-09-23T00:00:00-04:00", now)
    assert daily_data_is_current("2026-09-23T00:00:00", now)  # naive means New York
    assert daily_data_is_current("2026-09-24T00:00:00-04:00", now)  # today's forming bar
    assert not daily_data_is_current("2026-09-22T00:00:00-04:00", now)
    assert not daily_data_is_current(None, now)
    assert not daily_data_is_current("not a time", now)


def test_a_midnight_daily_stamp_is_as_of_that_days_close() -> None:
    assert daily_bar_reference_time("2026-09-23T00:00:00-04:00") == _et(2026, 9, 23, 16, 0)
    assert daily_bar_reference_time("2026-09-23T16:00:00-04:00") == _et(2026, 9, 23, 16, 0)


# --- the age and the badge -----------------------------------------------------------


def test_registry_measures_a_daily_bar_from_its_close() -> None:
    stamp = "2026-09-23T00:00:00"
    plain = _freshness_seconds(stamp)
    daily = _freshness_seconds(stamp, daily=True)
    assert plain is not None and daily is not None
    assert abs((plain - daily) - 16 * 3600) < 5


def _daily_item(session_date: date) -> dict:
    return {
        "tool": "get_price_statistics",
        "ok": True,
        "provider": "webull",
        "timeframe": "1d",
        "source_timestamp": f"{session_date.isoformat()}T00:00:00",
        "freshness_seconds": 66_000.0,
    }


def test_latest_daily_close_is_recent_not_stale() -> None:
    latest = us_market_calendar.latest_completed_session_date()

    quality = _item_quality(_daily_item(latest))

    assert (quality.state, quality.freshness_status) == ("verified", "recent")


def test_an_old_daily_close_is_still_stale() -> None:
    old = us_market_calendar.latest_completed_session_date() - timedelta(days=10)

    quality = _item_quality(_daily_item(old))

    assert (quality.state, quality.freshness_status) == ("stale", "stale")


def test_intraday_evidence_keeps_the_fifteen_minute_limit() -> None:
    item = {**_daily_item(us_market_calendar.latest_completed_session_date()), "timeframe": "5m"}

    assert _item_quality(item).freshness_status == "stale"


# --- the chat routes ---------------------------------------------------------------


def _comparison_result(request, *, session_date: date) -> ToolResult:
    return ToolResult(
        tool_name=request.tool_name,
        ok=True,
        data={"metric": "return_percent", "rankings": [{"symbol": "AAPL", "rank": 1, "value": 2.5}]},
        provider="MarketLens comparison",
        source_timestamp=f"{session_date.isoformat()}T00:00:00",
        freshness_seconds=18.4 * 3600,
        timeframe="1d",
    )


def _comparison():
    return ChatReplyResponse(
        reply="Verified semantic route",
        grounded=True,
        action="compare_symbols",
        action_tool_arguments={"symbols": ["AAPL", "MSFT"], "timeframe": "1d"},
    )


def test_daily_comparison_is_usable_during_the_session(monkeypatch) -> None:
    """The comparison gate withheld any daily ranking older than 15 minutes
    unless the market was closed, so it failed all through the session."""
    latest = us_market_calendar.latest_completed_session_date()
    monkeypatch.setattr("backend.ai.chat_actions._is_regular_market_closed", lambda: False)
    monkeypatch.setattr(
        "backend.ai.chat_actions.default_registry.execute",
        lambda request: _comparison_result(request, session_date=latest),
    )

    text, grounded = _run_market_tool(None, _comparison())

    assert grounded is True
    assert "AAPL's return is 2.50%" in text
    assert "session in progress isn't included" in text
    assert "market close" not in text


def test_an_old_daily_comparison_is_still_withheld_during_the_session(monkeypatch) -> None:
    old = us_market_calendar.latest_completed_session_date() - timedelta(days=10)
    monkeypatch.setattr("backend.ai.chat_actions._is_regular_market_closed", lambda: False)
    monkeypatch.setattr(
        "backend.ai.chat_actions.default_registry.execute",
        lambda request: _comparison_result(request, session_date=old),
    )

    text, grounded = _run_market_tool(None, _comparison())

    assert grounded is False
    assert "stale" in text or "old" in text


def test_price_statistics_requests_are_scoped_to_daily_bars(monkeypatch) -> None:
    requests = []

    def execute(request):
        requests.append(request)
        return ToolResult(tool_name=request.tool_name, ok=False, error="offline")

    monkeypatch.setattr("backend.ai.chat_actions.default_registry.execute", execute)
    parsed = ChatReplyResponse(
        reply="Verified semantic route",
        grounded=True,
        action="get_price_statistics",
        action_tool_arguments={"symbol": "TSLA", "metric": "max_drawdown"},
    )

    _run_market_tool(None, parsed)

    assert requests[0].timeframe == "1d"
