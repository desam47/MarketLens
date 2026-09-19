"""
Tests for the latest-bar / latest-bars / quote / status routes in
``backend.api.market_data_routes``.

Two things changed there and both need guarding:

* The Redis + SQLite tiers of the bar lookups are blocking I/O. They used to
  run directly on the event loop (``/bars`` loops over every timeframe and is
  polled by the frontend); they now run in one worker-thread hop.
* The handlers no longer declare an unused ``Depends(get_db)`` (a threadpool hop
  plus a Session per request, ~176 us measured, on the hottest polled routes).

Behaviour (tier order, fallbacks, response key order) must be unchanged.
"""
import asyncio
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.database import get_db
from backend.models.market_data import Bar, DataStatus


def _bar(symbol: str, timeframe: str, close: float, provider: str) -> Bar:
    return Bar(
        symbol=symbol, timestamp=datetime(2026, 9, 18, 15, 0), open=close, high=close,
        low=close, close=close, volume=100, timeframe=timeframe, provider=provider,
        data_status=DataStatus.LIVE,
    )


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


class _RouteTestCase(unittest.TestCase):
    def setUp(self):
        self.redis = MagicMock(name="redis_cache")
        self.svc = MagicMock(name="ingestion_service")
        self.svc.timeframes = ["1m", "5m", "1h"]
        self.mgr = MagicMock(name="market_data_manager")
        self.redis.get_latest_bar.return_value = None
        self.svc.get_latest_bar.return_value = None
        for target, obj in (
            ("backend.api.market_data_routes._redis_cache", self.redis),
            ("backend.api.market_data_routes.ingestion_service", self.svc),
            ("backend.api.market_data_routes.market_data_manager", self.mgr),
            # get_latest_bar re-imports the manager inside the handler.
            ("backend.market_data.services.manager.market_data_manager", self.mgr),
        ):
            p = patch(target, obj)
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(app)


class TestLatestBar(_RouteTestCase):
    def test_redis_hit_short_circuits(self):
        self.redis.get_latest_bar.return_value = _bar("AAPL", "1m", 1.0, "redis")
        r = self.client.get("/api/market-data/bar/aapl/1m")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["provider"], "redis")
        self.redis.get_latest_bar.assert_called_once_with("AAPL", "1m")
        self.svc.get_latest_bar.assert_not_called()
        self.mgr.get_latest_bar.assert_not_called()

    def test_database_is_second_tier(self):
        self.svc.get_latest_bar.return_value = _bar("AAPL", "1m", 2.0, "db")
        r = self.client.get("/api/market-data/bar/AAPL/1m")
        self.assertEqual(r.json()["provider"], "db")
        self.mgr.get_latest_bar.assert_not_called()

    def test_provider_chain_is_last_resort(self):
        self.mgr.get_latest_bar.return_value = _bar("AAPL", "1m", 3.0, "provider")
        r = self.client.get("/api/market-data/bar/AAPL/1m")
        self.assertEqual(r.json()["provider"], "provider")
        self.mgr.get_latest_bar.assert_called_once_with("AAPL", "1m")

    def test_404_when_every_tier_misses(self):
        self.mgr.get_latest_bar.side_effect = RuntimeError("no data")
        r = self.client.get("/api/market-data/bar/AAPL/1m")
        self.assertEqual(r.status_code, 404)
        self.assertIn("no data", r.json()["detail"])


class TestLatestBars(_RouteTestCase):
    def test_merges_all_tiers_in_timeframe_order(self):
        # 1m from Redis, 5m from the DB, 1h only from the provider chain.
        self.redis.get_latest_bar.side_effect = lambda s, tf: (
            _bar(s, tf, 1.0, "redis") if tf == "1m" else None
        )
        self.svc.get_latest_bar.side_effect = lambda s, tf: (
            _bar(s, tf, 2.0, "db") if tf == "5m" else None
        )
        self.mgr.get_latest_bar.side_effect = lambda s, tf: _bar(s, tf, 3.0, "provider")

        r = self.client.get("/api/market-data/bars/aapl")

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(list(body), ["1m", "5m", "1h"], "key order must follow the timeframes")
        self.assertEqual([body[tf]["provider"] for tf in ("1m", "5m", "1h")],
                         ["redis", "db", "provider"])
        self.mgr.get_latest_bar.assert_called_once_with("AAPL", "1h")  # only the missing one

    def test_a_missing_timeframe_is_skipped_not_fatal(self):
        self.redis.get_latest_bar.side_effect = lambda s, tf: (
            _bar(s, tf, 1.0, "redis") if tf in ("1m", "1h") else None
        )
        self.mgr.get_latest_bar.side_effect = RuntimeError("provider down for 5m")
        r = self.client.get("/api/market-data/bars/AAPL")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(list(r.json()), ["1m", "1h"])

    def test_no_timeframes_returns_empty(self):
        self.svc.timeframes = []
        r = self.client.get("/api/market-data/bars/AAPL")
        self.assertEqual(r.json(), {})


class TestBlockingIoRunsOffTheEventLoop(_RouteTestCase):
    """The regression these changes fix: sync Redis/SQLite calls on the loop."""

    def _record(self, seen: list):
        def side_effect(*_args):
            seen.append(_on_event_loop())
            return None
        return side_effect

    def test_bar_lookup_tiers_run_in_a_worker_thread(self):
        seen: list[bool] = []
        self.redis.get_latest_bar.side_effect = self._record(seen)
        self.svc.get_latest_bar.side_effect = self._record(seen)
        self.mgr.get_latest_bar.return_value = _bar("AAPL", "1m", 1.0, "provider")
        self.assertEqual(self.client.get("/api/market-data/bar/AAPL/1m").status_code, 200)
        self.assertEqual(len(seen), 2)  # redis + db tiers were both consulted
        self.assertFalse(any(seen), "Redis/SQLite lookups ran on the event loop")

    def test_bars_lookup_tiers_run_in_a_worker_thread(self):
        seen: list[bool] = []
        self.redis.get_latest_bar.side_effect = self._record(seen)
        self.svc.get_latest_bar.side_effect = self._record(seen)
        self.mgr.get_latest_bar.return_value = _bar("AAPL", "1m", 1.0, "provider")
        self.assertEqual(self.client.get("/api/market-data/bars/AAPL").status_code, 200)
        self.assertEqual(len(seen), 6)  # 3 timeframes x (redis + db)
        self.assertFalse(any(seen), "Redis/SQLite lookups ran on the event loop")

    def test_bars_uses_a_single_thread_hop_for_the_local_tiers(self):
        """One hop for all timeframes, not one per timeframe."""
        with patch("backend.api.market_data_routes.asyncio.to_thread",
                   wraps=asyncio.to_thread) as hop:
            self.mgr.get_latest_bar.return_value = None
            self.mgr.get_latest_bar.side_effect = RuntimeError("none")
            self.client.get("/api/market-data/bars/AAPL")
        local_hops = [c for c in hop.call_args_list
                      if c.args and getattr(c.args[0], "__name__", "") == "_local_latest_bars"]
        self.assertEqual(len(local_hops), 1)


class TestNoUnusedSessionDependency(unittest.TestCase):
    """~176 us + a threadpool hop per request for a Session nothing used."""

    ROUTES = {
        "/api/market-data/quote/{symbol}",
        "/api/market-data/quote/{symbol}/history",
        "/api/market-data/bar/{symbol}/{timeframe}",
        "/api/market-data/bars/{symbol}",
        "/api/market-data/status/{symbol}",
    }

    def test_polled_routes_do_not_depend_on_get_db(self):
        # Inspect the router itself: FastAPI wraps included routers in an
        # opaque ``_IncludedRouter``, so ``app.routes`` isn't a flat list.
        from backend.api.market_data_routes import router

        seen = set()
        for route in router.routes:
            if getattr(route, "path", None) in self.ROUTES:
                seen.add(route.path)
                deps = [d.call for d in route.dependant.dependencies]
                self.assertNotIn(get_db, deps, route.path)
        self.assertEqual(seen, self.ROUTES, "route table changed; update this test")


if __name__ == "__main__":
    unittest.main()
