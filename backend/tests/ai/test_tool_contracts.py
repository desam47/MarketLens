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
    SymbolRequest,
    WatchlistRequest,
    get_alerts_tool,
    get_market_context_tool,
    get_sector_data_tool,
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
