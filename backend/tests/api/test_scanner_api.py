"""
Tests for the Scanner API endpoints.

The scanner itself is mocked at the import boundary
(``backend.api.scanner.router.market_scanner``) so the endpoints can be
exercised in isolation. The real scanner is covered by
``backend/tests/scanner/test_scanner.py``.
"""
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api import ttl_cache as _ttl_cache_module
from backend.models.market_data import DataStatus, Quote
from backend.scanner.scanner import ScanResult


def _make_quote(symbol: str = "AAPL", price: float = 150.0) -> Quote:
    return Quote(
        symbol=symbol,
        price=price,
        timestamp=datetime(2025, 1, 1, 12, 0, 0),
        provider="yahoo_finance",
        data_status=DataStatus.DELAYED,
        volume=1_000_000,
        bid=price - 0.5,
        ask=price + 0.5,
    )


def _make_result(
    symbol: str = "AAPL",
    indicators: dict | None = None,
    scores: dict | None = None,
    signals: list[str] | None = None,
    rank: int | None = None,
) -> ScanResult:
    result = ScanResult(symbol, datetime(2025, 1, 1, 12, 0, 0))
    result.quote = _make_quote(symbol)
    for k, v in (indicators or {
        "price": 150.0,
        "volume": 1_000_000,
        "rsi": 55.0,
        "macd": 1.2,
        "adx": 22.0,
    }).items():
        result.add_indicator(k, v)
    for k, v in (scores or {"momentum": 60.0, "volume": 80.0}).items():
        result.add_score(k, v)
    result.signals = list(signals or ["RSI_OVERSOLD"])
    result.rank = rank
    return result


class TestScannerAPI(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        # Mock the scanner singleton the router imports.
        self.scanner_patch = patch('backend.api.scanner.router.market_scanner')
        self.mock_scanner = self.scanner_patch.start()
        # Mock the DB dependency to keep the watchlist endpoints self-contained.
        self.db_patch = patch('backend.api.dependencies.get_db')
        self.mock_get_db = self.db_patch.start()
        self.mock_db = MagicMock()
        self.mock_get_db.return_value = self.mock_db
        # Mock the watchlist repository used by the watchlist endpoints.
        self.repo_patch = patch('backend.api.scanner.router.WatchlistRepository')
        self.mock_repo_class = self.repo_patch.start()
        self.mock_repo = MagicMock()
        self.mock_repo_class.return_value = self.mock_repo
        # scan_symbols_async delegates to scan_symbols for the async routes.
        self.mock_scanner.scan_symbols_async = AsyncMock(
            side_effect=lambda symbols: self.mock_scanner.scan_symbols(symbols)
        )
        # Clear the in-process TTL caches so a previous test's mock
        # doesn't leak into this one's assertions.
        _ttl_cache_module._scan_cache.clear()
        _ttl_cache_module._regime_cache.clear()
        _ttl_cache_module._trend_cache.clear()
        _ttl_cache_module._quote_cache.clear()

    def tearDown(self):
        self.scanner_patch.stop()
        self.db_patch.stop()
        self.repo_patch.stop()

    # --- /api/scanner/{symbol} --------------------------------------------

    def test_scan_symbol_returns_full_result(self):
        result = _make_result("AAPL")
        self.mock_scanner.scan_symbol.return_value = result

        response = self.client.get("/api/scanner/AAPL")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["symbol"], "AAPL")
        self.assertEqual(data["rank"], None)
        self.assertEqual(data["signals"], ["RSI_OVERSOLD"])
        self.assertEqual(data["indicator_values"]["rsi"], 55.0)
        self.assertEqual(data["indicator_values"]["macd"], 1.2)
        self.assertEqual(data["indicator_values"]["adx"], 22.0)
        # scores + total_score both populated
        self.assertIn("momentum", data["scores"])
        self.assertIn("volume", data["scores"])
        # total_score uses the equal-weight path the scanner exposes by default
        self.assertIsInstance(data["total_score"], (int, float))
        # quote was serialized
        self.assertIsNotNone(data["quote"])
        self.assertEqual(data["quote"]["symbol"], "AAPL")
        self.assertEqual(data["quote"]["price"], 150.0)
        # Scanner was called with upper-case symbol (the router normalizes)
        self.mock_scanner.scan_symbol.assert_called_once_with("AAPL")

    def test_scan_symbol_uppercases_input(self):
        """Lower-case path input is normalized to upper-case before scanning."""
        result = _make_result("AAPL")
        self.mock_scanner.scan_symbol.return_value = result

        response = self.client.get("/api/scanner/aapl")

        self.assertEqual(response.status_code, 200)
        self.mock_scanner.scan_symbol.assert_called_once_with("AAPL")

    def test_scan_symbol_handles_exception(self):
        """A scanner exception becomes a 500 with the message as detail."""
        self.mock_scanner.scan_symbol.side_effect = RuntimeError("upstream down")

        response = self.client.get("/api/scanner/AAPL")

        self.assertEqual(response.status_code, 500)
        self.assertIn("upstream down", response.json()["detail"])

    def test_scan_symbol_handles_missing_quote(self):
        """A scan that returned no quote still serializes (quote: null)."""
        result = _make_result("AAPL")
        result.quote = None
        self.mock_scanner.scan_symbol.return_value = result

        response = self.client.get("/api/scanner/AAPL")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["quote"])

    # --- /api/scanner/{symbol}/cached -------------------------------------

    def test_cached_scan_returns_stored_result(self):
        result = _make_result("AAPL")
        self.mock_scanner.get_scan_result.return_value = result

        response = self.client.get("/api/scanner/AAPL/cached")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["symbol"], "AAPL")
        self.assertEqual(data["indicator_values"]["rsi"], 55.0)
        self.mock_scanner.get_scan_result.assert_called_once_with("AAPL")
        # Critically, the cached endpoint must NOT trigger a new scan.
        self.mock_scanner.scan_symbol.assert_not_called()

    def test_cached_scan_returns_null_when_missing(self):
        """A symbol never scanned in this process returns a JSON null body."""
        self.mock_scanner.get_scan_result.return_value = None

        response = self.client.get("/api/scanner/NOSYMBOL/cached")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json())

    # --- /api/scanner/signals/{symbol} ------------------------------------

    def test_signals_returns_known_signals(self):
        result = _make_result("AAPL", signals=["RSI_OVERSOLD", "MACD_BULLISH"])
        self.mock_scanner.get_scan_result.return_value = result

        response = self.client.get("/api/scanner/signals/AAPL")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), ["RSI_OVERSOLD", "MACD_BULLISH"])
        # No new scan needed when cache hits.
        self.mock_scanner.scan_symbol.assert_not_called()

    def test_signals_triggers_scan_on_cache_miss(self):
        """If the symbol is not yet cached, the endpoint scans it first."""
        cached = _make_result("AAPL", signals=["RSI_OVERBOUGHT"])
        self.mock_scanner.get_scan_result.return_value = None
        self.mock_scanner.scan_symbol.return_value = cached

        response = self.client.get("/api/scanner/signals/AAPL")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), ["RSI_OVERBOUGHT"])
        self.mock_scanner.scan_symbol.assert_called_once_with("AAPL")

    def test_signals_handles_scan_failure(self):
        self.mock_scanner.get_scan_result.return_value = None
        self.mock_scanner.scan_symbol.side_effect = RuntimeError("network")

        response = self.client.get("/api/scanner/signals/AAPL")

        self.assertEqual(response.status_code, 500)
        self.assertIn("network", response.json()["detail"])

    # --- /api/scanner/watchlist/{id} --------------------------------------

    def test_watchlist_scan_404s_when_missing(self):
        self.mock_repo.get_watchlist.return_value = None

        response = self.client.get("/api/scanner/watchlist/999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Watchlist not found")
        # Don't scan anything if the watchlist doesn't exist.
        self.mock_scanner.scan_symbols.assert_not_called()

    def test_watchlist_scan_returns_empty_for_no_symbols(self):
        self.mock_repo.get_watchlist.return_value = MagicMock(id=1)
        self.mock_repo.get_all_watchlist_symbols.return_value = []

        response = self.client.get("/api/scanner/watchlist/1")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["results"], [])
        # The scanner should not have been touched.
        self.mock_scanner.scan_symbols.assert_not_called()

    def test_watchlist_scan_ranks_and_returns(self):
        watchlist = MagicMock(id=1)
        aapl = MagicMock(symbol="AAPL", is_enabled=True)
        googl = MagicMock(symbol="GOOGL", is_enabled=True)
        msft = MagicMock(symbol="MSFT", is_enabled=True)

        self.mock_repo.get_watchlist.return_value = watchlist
        # The new code path queries the unfiltered list so the UI can show
        # disabled rows for re-enable/delete management.
        self.mock_repo.get_all_watchlist_symbols.return_value = [aapl, googl, msft]

        # Each scan_symbol call returns a distinct result.
        aapl_result = _make_result("AAPL", rank=1, indicators={"rsi": 60.0})
        googl_result = _make_result("GOOGL", rank=2, indicators={"rsi": 55.0})
        msft_result = _make_result("MSFT", rank=3, indicators={"rsi": 50.0})
        self.mock_scanner.scan_symbol.side_effect = [
            aapl_result, googl_result, msft_result
        ]
        # scan_symbols populates the in-memory cache the router reads from.
        def _populate(symbols):
            for sym in symbols:
                result = self.mock_scanner.scan_symbol(sym)
                self.mock_scanner.scan_results[sym] = result
            self.mock_scanner.last_scan_time = datetime(2025, 1, 1, 12, 0, 0)
            return [self.mock_scanner.scan_results[s] for s in symbols]
        self.mock_scanner.scan_symbols.side_effect = _populate
        # rank_symbols returns (sym, score) tuples in ranked order.
        self.mock_scanner.rank_symbols.return_value = [
            ("AAPL", 80.0), ("GOOGL", 70.0), ("MSFT", 60.0)
        ]
        # scan_results exposes the result objects the router reads by symbol.
        self.mock_scanner.scan_results = {
            "AAPL": aapl_result,
            "GOOGL": googl_result,
            "MSFT": msft_result,
        }

        response = self.client.get("/api/scanner/watchlist/1")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 3)
        # Order matches the ranked list, not the scan order.
        self.assertEqual([r["symbol"] for r in data["results"]], ["AAPL", "GOOGL", "MSFT"])
        # All enabled rows report is_enabled=True.
        for r in data["results"]:
            self.assertTrue(r["is_enabled"])
        # Timestamp is serialized (not empty).
        self.assertNotEqual(data["timestamp"], "")
        # The watchlist symbols query used the all-inclusive variant.
        self.mock_repo.get_all_watchlist_symbols.assert_called_once_with(
            1, include_disabled=True
        )

    def test_watchlist_scan_passes_enabled_only(self):
        """The router should call the inclusive variant so disabled rows
        are visible (with is_enabled=False) for management."""
        self.mock_repo.get_watchlist.return_value = MagicMock(id=1)
        self.mock_repo.get_all_watchlist_symbols.return_value = []

        self.client.get("/api/scanner/watchlist/1")

        self.mock_repo.get_all_watchlist_symbols.assert_called_once_with(
            1, include_disabled=True
        )

    # --- /api/scanner/watchlist/{id}/top ----------------------------------

    def test_watchlist_top_returns_ranked_subset(self):
        watchlist = MagicMock(id=1)
        symbols = [MagicMock(symbol=s) for s in ("AAPL", "GOOGL", "MSFT")]
        self.mock_repo.get_watchlist.return_value = watchlist
        self.mock_repo.get_watchlist_symbols.return_value = symbols

        aapl = _make_result("AAPL")
        googl = _make_result("GOOGL")
        msft = _make_result("MSFT")
        self.mock_scanner.scan_results = {"AAPL": aapl, "GOOGL": googl, "MSFT": msft}
        self.mock_scanner.scan_symbols.return_value = [aapl, googl, msft]
        self.mock_scanner.last_scan_time = datetime(2025, 1, 1, 12, 0, 0)
        self.mock_scanner.rank_symbols.return_value = [
            ("AAPL", 90.0), ("GOOGL", 70.0), ("MSFT", 50.0),
        ]

        response = self.client.get("/api/scanner/watchlist/1/top?limit=2")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)
        self.assertEqual([r["symbol"] for r in data], ["AAPL", "GOOGL"])

    def test_watchlist_top_default_limit_is_10(self):
        """Without a limit query param, the default of 10 is applied."""
        self.mock_repo.get_watchlist.return_value = MagicMock(id=1)
        self.mock_repo.get_watchlist_symbols.return_value = []

        response = self.client.get("/api/scanner/watchlist/1/top")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_watchlist_top_404_when_watchlist_missing(self):
        self.mock_repo.get_watchlist.return_value = None

        response = self.client.get("/api/scanner/watchlist/999/top")

        self.assertEqual(response.status_code, 404)


class TestScannerFilterAPI(unittest.TestCase):
    """Phase 10: tests for the composable-filter endpoints."""

    def setUp(self):
        self.client = TestClient(app)
        self.scanner_patch = patch('backend.api.scanner.router.market_scanner')
        self.mock_scanner = self.scanner_patch.start()

    def tearDown(self):
        self.scanner_patch.stop()

    def test_list_filter_types(self):
        response = self.client.get("/api/scanner/filter-types")
        self.assertEqual(response.status_code, 200)
        types = response.json()
        self.assertIn("daily_bullish", types)
        self.assertIn("trend_score_gt", types)
        self.assertIn("mtf_alignment", types)

    def test_filter_returns_matching_results(self):
        aapl = _make_result("AAPL", scores={"momentum": 80.0, "volume": 60.0})
        aapl.scores["_total"] = 85.0
        msft = _make_result("MSFT", scores={"momentum": 30.0, "volume": 20.0})
        msft.scores["_total"] = 25.0
        self.mock_scanner.scan_results = {"AAPL": aapl, "MSFT": msft}

        response = self.client.post(
            "/api/scanner/filter",
            json={
                "filters": [{"type": "trend_score_gt", "params": {"threshold": 50.0}}],
                "match": "AND",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        symbols = {r["symbol"] for r in body}
        self.assertEqual(symbols, {"AAPL"})

    def test_filter_unknown_type_returns_500(self):
        # FastAPI surfaces unhandled ValueError as 500; the goal is that
        # the error doesn't crash the process.
        response = self.client.post(
            "/api/scanner/filter",
            json={"filters": [{"type": "does_not_exist"}]},
        )
        self.assertIn(response.status_code, (400, 422, 500))

    def test_filter_with_and_conjunction(self):
        aapl = _make_result("AAPL", scores={"momentum": 80.0, "volume": 80.0})
        aapl.scores["_total"] = 80.0
        aapl.trend_signals = {"ONE_DAY": {"direction": "uptrend", "confidence": 0.8}}
        msft = _make_result("MSFT", scores={"momentum": 80.0, "volume": 80.0})
        msft.scores["_total"] = 80.0
        msft.trend_signals = {"ONE_DAY": {"direction": "downtrend", "confidence": 0.8}}
        self.mock_scanner.scan_results = {"AAPL": aapl, "MSFT": msft}

        response = self.client.post(
            "/api/scanner/filter",
            json={
                "filters": [
                    {"type": "trend_score_gt", "params": {"threshold": 50.0}},
                    {"type": "daily_bullish", "params": {"min_confidence": 0.5}},
                ],
                "match": "AND",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["symbol"], "AAPL")

    def test_filter_with_or_disjunction(self):
        aapl = _make_result("AAPL", indicators={"rsi": 25.0})
        msft = _make_result("MSFT", indicators={"rsi": 80.0})
        goog = _make_result("GOOG", indicators={"rsi": 50.0})
        self.mock_scanner.scan_results = {"AAPL": aapl, "MSFT": msft, "GOOG": goog}

        response = self.client.post(
            "/api/scanner/filter",
            json={
                "filters": [
                    {"type": "rsi_oversold", "params": {"threshold": 30.0}},
                    {"type": "rsi_overbought", "params": {"threshold": 70.0}},
                ],
                "match": "OR",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        symbols = {r["symbol"] for r in body}
        self.assertEqual(symbols, {"AAPL", "MSFT"})


class TestScannerRankingsAPI(unittest.TestCase):
    """Phase 10: tests for the named-rankings endpoints."""

    def setUp(self):
        self.client = TestClient(app)
        self.scanner_patch = patch('backend.api.scanner.router.market_scanner')
        self.mock_scanner = self.scanner_patch.start()
        self.engine_patch = patch('backend.api.scanner.router.default_ranking_engine')
        self.mock_engine = self.engine_patch.start()
        # Replace the MagicMock's CATEGORIES with the real engine's list
        # so the categories endpoint returns the real names by default.
        from backend.scanner.ranking import default_ranking_engine as real_engine
        self.mock_engine.CATEGORIES = real_engine.CATEGORIES

    def tearDown(self):
        self.scanner_patch.stop()
        self.engine_patch.stop()

    def test_list_categories(self):
        response = self.client.get("/api/scanner/rankings/categories")
        self.assertEqual(response.status_code, 200)
        cats = response.json()
        names = {c["name"] for c in cats}
        self.assertIn("strongest_bullish", names)
        self.assertIn("best_mtf_alignment", names)

    def test_rankings_endpoint_returns_payload(self):
        # Mock the engine to avoid coupling this test to ranking logic
        from backend.scanner.ranking import NamedRanking, RankedEntry
        self.mock_engine.CATEGORIES = [
            {"name": "strongest_bullish", "label": "Strongest Bullish", "description": "x"},
        ]
        self.mock_engine.rank.return_value = {
            "strongest_bullish": NamedRanking(
                name="strongest_bullish",
                label="Strongest Bullish",
                description="x",
                entries=[RankedEntry(symbol="AAPL", score=85.0, rank=1, metrics={})],
                total_eligible=1,
            ),
        }
        self.mock_scanner.scan_results = {"AAPL": _make_result("AAPL")}

        response = self.client.post(
            "/api/scanner/rankings",
            json={"filters": [], "match": "AND"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["name"], "strongest_bullish")
        self.assertEqual(body[0]["entries"][0]["symbol"], "AAPL")

    def test_watchlist_rankings_404_when_missing(self):
        with patch('backend.api.scanner.router.WatchlistRepository') as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_watchlist.return_value = None

            response = self.client.get("/api/scanner/watchlist/999/rankings")
            self.assertEqual(response.status_code, 404)


class TestScannerScopeAndMatchAll(unittest.TestCase):
    """Regression tests for two Live Scanner bugs found live 2026-09-13.

    Both were invisible in the existing suite because nothing tested them:

    1. ``router._build_filter([], match)`` returned
       ``DailyBullish(min_confidence=-1.0)`` while its docstring claimed
       match-all. ``TimeframeDirection.matches()`` gates on
       ``direction == "uptrend"`` unconditionally, so a negative confidence
       floor could not disable it — "match all" silently returned only the
       symbols whose daily trend happened to be an uptrend (live: 2 of 10).
       The fix returns ``TrueFilter``. ``TrueFilter`` itself was already
       covered by ``backend/tests/scanner/test_filters.py``; the router's
       fallback that chose the wrong filter was not.

    2. ``/filter``, ``/rankings`` and ``/top-movers`` read the module-global
       ``market_scanner.scan_results`` directly, which accumulates every
       symbol ever scanned. An explicit ``symbols`` scope therefore narrowed
       only the optional pre-scan, never the pool that was filtered or
       ranked (live: ``POST /rankings`` with ``symbols=["SPY"]`` still
       reported ``total_eligible`` for all 10 cached symbols). The fix routes
       every scoped read through ``_scoped_cache``.
    """

    def setUp(self):
        self.client = TestClient(app)
        self.scanner_patch = patch('backend.api.scanner.router.market_scanner')
        self.mock_scanner = self.scanner_patch.start()
        # The scoped endpoints await a pre-scan before reading the cache.
        self.mock_scanner.scan_symbols_async = AsyncMock()

    def tearDown(self):
        self.scanner_patch.stop()

    # --- _build_filter: empty list is a genuine match-all -----------------

    def test_build_filter_empty_list_returns_true_filter(self):
        from backend.api.scanner.router import _build_filter
        from backend.scanner.filters import TrueFilter

        self.assertIsInstance(_build_filter([], "AND"), TrueFilter)
        self.assertIsInstance(_build_filter([], "OR"), TrueFilter)

    def test_build_filter_empty_list_matches_downtrend_symbol(self):
        """The exact case the old ``DailyBullish(min_confidence=-1.0)``
        fallback wrongly rejected: a symbol in a daily downtrend."""
        from backend.api.scanner.router import _build_filter

        downtrend = _make_result("AAPL")
        downtrend.trend_signals = {
            "ONE_DAY": {"direction": "downtrend", "confidence": 0.9}
        }

        f = _build_filter([], "AND")

        self.assertTrue(f.matches(downtrend))
        self.assertEqual(f.describe(), "match all")

    def test_filter_endpoint_empty_filters_returns_every_symbol(self):
        """Live regression: ``{"filters": []}`` returned 2 of 10 symbols
        while ``{"type": "true"}`` returned all 10."""
        uptrend = _make_result("AAPL")
        uptrend.trend_signals = {"ONE_DAY": {"direction": "uptrend", "confidence": 0.9}}
        downtrend = _make_result("MSFT")
        downtrend.trend_signals = {"ONE_DAY": {"direction": "downtrend", "confidence": 0.9}}
        self.mock_scanner.scan_results = {"AAPL": uptrend, "MSFT": downtrend}

        empty = self.client.post("/api/scanner/filter", json={"filters": [], "match": "AND"})
        explicit = self.client.post(
            "/api/scanner/filter",
            json={"filters": [{"type": "true", "params": {}}], "match": "AND"},
        )

        self.assertEqual(empty.status_code, 200)
        self.assertEqual(
            {r["symbol"] for r in empty.json()},
            {r["symbol"] for r in explicit.json()},
        )
        self.assertEqual({r["symbol"] for r in empty.json()}, {"AAPL", "MSFT"})

    def test_build_filter_nonempty_list_still_applies(self):
        """A real filter list must still narrow — the fix only changed the
        empty-list branch."""
        from backend.api.scanner.router import _build_filter, _FilterRequest

        uptrend = _make_result("AAPL")
        uptrend.trend_signals = {"ONE_DAY": {"direction": "uptrend", "confidence": 0.9}}
        downtrend = _make_result("MSFT")
        downtrend.trend_signals = {"ONE_DAY": {"direction": "downtrend", "confidence": 0.9}}

        f = _build_filter([_FilterRequest(type="daily_bullish", params={})], "AND")

        self.assertTrue(f.matches(uptrend))
        self.assertFalse(f.matches(downtrend))

    # --- symbols scope narrows the pool, not just the pre-scan ------------

    def _three_symbol_cache(self) -> None:
        """AAPL/MSFT cached; GOOG cached but never in scope."""
        self.mock_scanner.scan_results = {
            "AAPL": _make_result("AAPL"),
            "MSFT": _make_result("MSFT"),
            "GOOG": _make_result("GOOG"),
        }

    def test_filter_endpoint_symbols_scope_narrows_result_set(self):
        self._three_symbol_cache()

        response = self.client.post(
            "/api/scanner/filter",
            json={"filters": [{"type": "true", "params": {}}], "match": "AND"},
            params={"symbols": ["AAPL"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["symbol"] for r in response.json()], ["AAPL"])

    def test_rankings_endpoint_empty_filters_eligible_is_full_pool(self):
        """The NamedRankingsPanel call shape: empty filters, no scope."""
        self._three_symbol_cache()

        response = self.client.post("/api/scanner/rankings", json={"filters": [], "match": "AND"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body)
        # Some categories now have sign-guards (e.g. strongest_bearish),
        # so total_eligible may be < 3 for those. We check that it is <= 3
        # and that at least one category (like strongest_bullish) remains 3.
        eligibles = {r["total_eligible"] for r in body}
        self.assertTrue(eligibles.issubset({0, 1, 2, 3}))
        self.assertIn(3, eligibles)

    def test_rankings_endpoint_symbols_scope_narrows_pool(self):
        """Live regression: ``symbols=["SPY"]`` still reported every cached
        symbol as eligible."""
        self._three_symbol_cache()

        response = self.client.post(
            "/api/scanner/rankings",
            json={"filters": [], "match": "AND"},
            params={"symbols": ["AAPL"]},
        )

        self.assertEqual(response.status_code, 200)
        for ranking in response.json():
            # Eligible count should be <= 1 because only AAPL is in scope.
            # Categories with guards might be 0.
            self.assertTrue(ranking["total_eligible"] <= 1)
            self.assertTrue(
                {e["symbol"] for e in ranking["entries"]} <= {"AAPL"}
            )

    def test_scoped_cache_passes_through_without_symbols(self):
        from backend.api.scanner.router import _scoped_cache

        self._three_symbol_cache()

        self.assertEqual(len(_scoped_cache(None)), 3)
        self.assertEqual(len(_scoped_cache([])), 3)

    def test_scoped_cache_narrows_case_insensitively(self):
        from backend.api.scanner.router import _scoped_cache

        self._three_symbol_cache()

        narrowed = _scoped_cache(["aapl"])

        self.assertEqual([r.symbol for r in narrowed], ["AAPL"])

    def test_top_movers_stays_within_watchlist_symbols(self):
        """A symbol in the global cache but absent from the watchlist must
        not leak into the ranked output."""
        self._three_symbol_cache()
        self.mock_scanner.last_scan_time = datetime(2025, 1, 1, 12, 0, 0)

        with patch('backend.api.scanner.router.WatchlistRepository') as repo_class:
            repo = MagicMock()
            repo_class.return_value = repo
            repo.get_watchlists.return_value = [MagicMock(id=1)]
            repo.get_watchlist.return_value = MagicMock(id=1)
            repo.get_watchlist_symbols.return_value = [
                MagicMock(symbol="AAPL"), MagicMock(symbol="MSFT"),
            ]

            response = self.client.get("/api/scanner/top-movers?direction=bullish")

        self.assertEqual(response.status_code, 200)
        symbols = {r["symbol"] for r in response.json()}
        self.assertTrue(symbols)
        self.assertNotIn("GOOG", symbols)
        self.assertTrue(symbols <= {"AAPL", "MSFT"})


if __name__ == '__main__':
    unittest.main()
