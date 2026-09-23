from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from backend.ai.market_tools import (
    WatchlistIntelligenceRequest,
    get_watchlist_intelligence_tool,
)


def test_watchlist_intelligence_tool_reuses_scanner_cache(monkeypatch) -> None:
    db = Mock()
    watchlist = SimpleNamespace(id=7, name="Core", is_active=True)
    scan_result = object()
    repository = Mock()
    repository.get_watchlist.return_value = watchlist
    repository.get_watchlist_symbols.return_value = [SimpleNamespace(symbol="AAPL")]

    monkeypatch.setattr("backend.database.SessionLocal", lambda: db)
    monkeypatch.setattr("backend.repositories.watchlist_repository.WatchlistRepository", lambda _: repository)
    monkeypatch.setattr(
        "backend.scanner.scanner.market_scanner",
        SimpleNamespace(scan_results={"AAPL": scan_result}),
    )
    builder = Mock(
        return_value={
            "data_status": "ready",
            "watchlist_size": 1,
            "analyzed_symbols": 1,
            "session_scope": "all",
            "price_basis": "scanner",
            "top_bearish": [],
            "top_bullish": [],
            "breakouts": [],
            "deteriorating": [],
            "volume_spikes": [],
            "relative_strength": [],
            "mtf_alignment": [],
            "sector_rotation": [],
            "warnings": [],
        }
    )
    monkeypatch.setattr("backend.scanner.watchlist_intelligence.build_watchlist_intelligence", builder)

    result = get_watchlist_intelligence_tool(
        WatchlistIntelligenceRequest(watchlist_id=7, concern="weak")
    ).model_dump()

    assert result["watchlist_name"] == "Core"
    assert result["concern"] == "weak"
    assert result["provider_request_count"] == 0
    builder.assert_called_once_with(
        [scan_result],
        watchlist_size=1,
        session_scope="all",
        top_n=5,
    )
    db.close.assert_called_once()


def test_watchlist_intelligence_warms_missing_scanner_symbols(monkeypatch) -> None:
    db = Mock()
    watchlist = SimpleNamespace(id=7, name="Core", is_active=True)
    scan_result = object()
    repository = Mock()
    repository.get_watchlist.return_value = watchlist
    repository.get_watchlist_symbols.return_value = [SimpleNamespace(symbol="AAPL")]

    scanner = SimpleNamespace(scan_results={})

    async def scan_missing(symbols):
        assert symbols == ["AAPL"]
        scanner.scan_results["AAPL"] = scan_result

    scanner.scan_symbols_async = AsyncMock(side_effect=scan_missing)
    monkeypatch.setattr("backend.database.SessionLocal", lambda: db)
    monkeypatch.setattr("backend.repositories.watchlist_repository.WatchlistRepository", lambda _: repository)
    monkeypatch.setattr("backend.scanner.scanner.market_scanner", scanner)
    builder = Mock(
        return_value={
            "data_status": "ready",
            "watchlist_size": 1,
            "analyzed_symbols": 1,
            "session_scope": "all",
            "price_basis": "scanner",
            "top_bearish": [],
            "top_bullish": [],
            "breakouts": [],
            "deteriorating": [],
            "volume_spikes": [],
            "relative_strength": [],
            "mtf_alignment": [],
            "sector_rotation": [],
            "warnings": [],
        }
    )
    monkeypatch.setattr("backend.scanner.watchlist_intelligence.build_watchlist_intelligence", builder)

    result = get_watchlist_intelligence_tool(
        WatchlistIntelligenceRequest(watchlist_id=7, concern="weak")
    ).model_dump()

    scanner.scan_symbols_async.assert_awaited_once_with(["AAPL"])
    assert result["analyzed_symbols"] == 1
    assert result["provider_request_count"] == 1
    db.close.assert_called_once()


def test_watchlist_intelligence_aggregates_multiple_active_lists(monkeypatch) -> None:
    db = Mock()
    watchlists = [
        SimpleNamespace(id=1, name="Core", is_active=True),
        SimpleNamespace(id=2, name="Swing", is_active=True),
    ]
    scan_results = {symbol: object() for symbol in ("AAPL", "MSFT", "NVDA")}
    repository = Mock()
    repository.get_watchlists.return_value = watchlists
    repository.get_watchlist_symbols.side_effect = [
        [SimpleNamespace(symbol="AAPL"), SimpleNamespace(symbol="MSFT")],
        [SimpleNamespace(symbol="MSFT"), SimpleNamespace(symbol="NVDA")],
    ]
    monkeypatch.setattr("backend.database.SessionLocal", lambda: db)
    monkeypatch.setattr("backend.repositories.watchlist_repository.WatchlistRepository", lambda _: repository)
    monkeypatch.setattr(
        "backend.scanner.scanner.market_scanner",
        SimpleNamespace(scan_results=scan_results),
    )
    builder = Mock(
        return_value={
            "data_status": "ready",
            "watchlist_size": 3,
            "analyzed_symbols": 3,
            "session_scope": "all",
            "price_basis": "scanner",
            "top_bearish": [],
            "top_bullish": [],
            "breakouts": [],
            "deteriorating": [],
            "volume_spikes": [],
            "relative_strength": [],
            "mtf_alignment": [],
            "sector_rotation": [],
            "warnings": [],
        }
    )
    monkeypatch.setattr("backend.scanner.watchlist_intelligence.build_watchlist_intelligence", builder)

    result = get_watchlist_intelligence_tool(
        WatchlistIntelligenceRequest(concern="weak")
    ).model_dump()

    assert result["watchlist_id"] is None
    assert result["watchlist_name"] == "All active watchlists"
    assert result["watchlist_size"] == 3
    builder.assert_called_once_with(
        [scan_results["AAPL"], scan_results["MSFT"], scan_results["NVDA"]],
        watchlist_size=3,
        session_scope="all",
        top_n=5,
    )
    db.close.assert_called_once()
