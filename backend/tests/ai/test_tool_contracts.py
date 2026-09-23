"""Contract tests: AI Hub tool output vs. the equivalent existing page/API.

Phase 5.2's own verification bar (v5_plan.md 5.2, "Verification") requires
tool output to be checked against the existing page/API output for the same
symbol and scope — not just unit-tested in isolation. These tests mount the
real routers (same pattern as backend/tests/regime/test_regime_api.py) and
call the AI tool directly, then assert the fields both an evidence-consuming
model and a human reading the page would see actually agree.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.market_tools import (
    AlertsRequest,
    ConfluenceRequest,
    SymbolRequest,
    TapeRequest,
    TrendRequest,
    WatchlistRequest,
    get_alerts_tool,
    get_confluence_tool,
    get_market_context_tool,
    get_relative_strength_tool,
    get_sector_data_tool,
    get_tape_state_tool,
    get_trend_tool,
    get_watchlist_tool,
)
from backend.database import SessionLocal
from backend.repositories.alert_repository import AlertRepository
from backend.repositories.watchlist_repository import WatchlistRepository


def test_sector_tool_matches_regime_sector_endpoint() -> None:
    from backend.api.regime.router import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    api_body = client.get("/api/regime/AAPL/sector").json()
    tool_result = get_sector_data_tool(SymbolRequest(symbol="AAPL"))

    # Same shared, cached SectorEngine backs both paths (backend/api/regime/
    # router.py::_get_sector_engine) — no ticks land between the two calls,
    # so every field must agree exactly, not just approximately.
    for key in ("symbol", "sector", "sector_etf", "stock_trend", "sector_trend", "market_trend", "alignment_score", "alignment_level"):
        assert getattr(tool_result, key) == api_body[key], f"{key} mismatch: tool={getattr(tool_result, key)!r} api={api_body[key]!r}"


def test_market_context_tool_matches_market_context_endpoint() -> None:
    from backend.api.market_context.router import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    api_body = client.get("/api/market-context/current").json()
    tool_result = get_market_context_tool(WatchlistRequest())

    for key in ("regime", "confidence", "trend_strength", "momentum", "volatility_state"):
        assert getattr(tool_result, key) == api_body[key], f"{key} mismatch: tool={getattr(tool_result, key)!r} api={api_body[key]!r}"


def test_watchlist_tool_matches_watchlist_api() -> None:
    from backend.api.dependencies import get_db
    from backend.api.watchlist.router import router

    app = FastAPI()
    app.include_router(router)

    def _override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(app)

    db = SessionLocal()
    repository = WatchlistRepository(db)
    try:
        watchlist = repository.create_watchlist(name="Contract Test Watchlist", description=None)
        repository.add_symbol_to_watchlist(watchlist.id, "AAPL")
        repository.add_symbol_to_watchlist(watchlist.id, "MSFT")

        api_watchlist = client.get(f"/api/watchlists/{watchlist.id}").json()
        api_symbols = client.get(f"/api/watchlists/{watchlist.id}/symbols").json()
        tool_result = get_watchlist_tool(WatchlistRequest(watchlist_id=watchlist.id))

        assert tool_result.id == api_watchlist["id"]
        assert tool_result.name == api_watchlist["name"]
        assert {row["symbol"] for row in tool_result.symbols} == {row["symbol"] for row in api_symbols}
        assert len(tool_result.symbols) == len(api_symbols)
    finally:
        repository.delete_watchlist(watchlist.id)
        db.close()


def test_alerts_tool_matches_alerts_api() -> None:
    from backend.api.alerts.router import router
    from backend.api.dependencies import get_db
    from backend.database import SessionLocal

    app = FastAPI()
    app.include_router(router)

    def _override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(app)

    repository = AlertRepository()
    try:
        repository.create(name="Contract Test Alert", symbol="AAPL", condition_type="price_above", parameter="999")

        api_alerts = client.get("/api/alerts/").json()
        tool_result = get_alerts_tool(AlertsRequest(symbol="AAPL"))

        api_by_id = {row["id"]: row for row in api_alerts if row["symbol"] == "AAPL"}
        tool_by_id = {row["id"]: row for row in tool_result.alerts}
        assert api_by_id.keys() == tool_by_id.keys()
        for alert_id, api_row in api_by_id.items():
            tool_row = tool_by_id[alert_id]
            for key in ("name", "symbol", "condition_type", "parameter", "is_enabled", "created_at", "updated_at"):
                assert tool_row[key] == api_row[key], f"{key} mismatch on alert {alert_id}: tool={tool_row[key]!r} api={api_row[key]!r}"
    finally:
        for row in repository.get_for_symbol("AAPL"):
            if row.name == "Contract Test Alert":
                repository.delete(row.id)
        repository.close()


def test_trend_tool_matches_trend_endpoint() -> None:
    from backend.api.trend.router import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Same shared TrendEngine registry backs both paths — whatever state
    # it's in (cold in the sandboxed test DB, or warm from an earlier test
    # in this session), the endpoint and the tool must describe it
    # identically, since they read the identical engine instance.
    api_body = client.get("/api/trend/AAPL/current/1d").json()
    tool_result = get_trend_tool(TrendRequest(symbol="AAPL", timeframe="1d"))

    for key in ("symbol", "timeframe", "direction", "strength", "confidence", "score", "data_status", "provider"):
        assert getattr(tool_result, key) == api_body[key], f"{key} mismatch: tool={getattr(tool_result, key)!r} api={api_body[key]!r}"


def test_confluence_tool_matches_confluence_endpoint() -> None:
    from backend.api.multitimeframe.router import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    api_body = client.get("/api/multitimeframe/AAPL/confluence").json()
    tool_result = get_confluence_tool(ConfluenceRequest(symbol="AAPL", preset="day_trading"))

    for key in ("symbol", "direction", "strength", "alignment_score", "preset", "short_term_direction", "intermediate_direction", "higher_direction"):
        assert getattr(tool_result, key) == api_body[key], f"{key} mismatch: tool={getattr(tool_result, key)!r} api={api_body[key]!r}"


def test_relative_strength_tool_matches_relative_strength_endpoint() -> None:
    from backend.api.regime.router import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    api_body = client.get("/api/regime/AAPL/relative-strength").json()
    tool_result = get_relative_strength_tool(SymbolRequest(symbol="AAPL"))

    assert tool_result.symbol == api_body["symbol"]
    assert tool_result.count == api_body["count"]
    # Each call recomputes signals fresh (datetime.now() at compute time,
    # like SectorEngine.get_current_signal() above), so timestamps
    # legitimately differ by microseconds between the two independent
    # calls — compare every field except that one.
    for tool_signal, api_signal in zip(tool_result.signals, api_body["signals"], strict=True):
        for key in ("symbol", "benchmark", "rs_pct", "classification", "symbol_return_pct", "benchmark_return_pct", "lookback_days"):
            assert tool_signal[key] == api_signal[key], f"{key} mismatch: tool={tool_signal[key]!r} api={api_signal[key]!r}"


def test_tape_state_tool_matches_disabled_tape_endpoint(monkeypatch) -> None:
    """Only the disabled-feature path is contract-tested live: an enabled
    get_tape_engine() call activates a background-thread Webull seed
    (backend/api/tape/registry.py::_schedule_seed), which the sandboxed
    test environment's network guard is not a safe target for. The
    enabled/live-snapshot path already has a mocked-engine unit test in
    test_market_tools.py.
    """
    from backend.api.tape.router import router
    from backend.config.settings import settings

    monkeypatch.setattr(settings.tape, "enabled", False)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    api_response = client.get("/api/tape/AAPL")
    assert api_response.status_code == 503

    try:
        get_tape_state_tool(TapeRequest(symbol="AAPL"))
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "TAPE_ENABLED" in str(exc)
