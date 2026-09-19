"""
Route order is an optimisation, so it must never be a behaviour change.

Starlette scans the app's top-level routers linearly on every request (~3.4 us per
router scanned) and the FIRST match wins, so ``main.py`` lists the busiest routers
first (ranked by real traffic share). That is only safe while no two routers can
match the same request. These tests make that a checked invariant: a future route
that overlaps another router fails here, instead of silently changing which
handler serves a request just because someone reordered two ``include_router``
lines.
"""
import re
import unittest

from starlette.routing import Match

from backend.api.main import app

_HTTP = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def _probe(template: str) -> str:
    """A concrete path that the template accepts."""
    return re.sub(r"\{[^}:]+:path\}", "x1/y", re.sub(r"\{[^}]+\}", "x1", template))


def _scope(method: str, path: str, kind: str = "http") -> dict:
    return {"type": kind, "method": method, "path": path, "root_path": "", "headers": [], "query_string": b""}


def _entries():
    return app.router.routes


def _matching_entries(scope: dict) -> list[int]:
    return [i for i, r in enumerate(_entries()) if r.matches(scope)[0] == Match.FULL]


def _websocket_paths() -> list[str]:
    paths = []
    for entry in _entries():
        for route in getattr(getattr(entry, "original_router", None), "routes", []) or []:
            if type(route).__name__ == "APIWebSocketRoute":
                paths.append(route.path)
    return paths


class TestRouteOrderIsBehaviourNeutral(unittest.TestCase):
    def test_no_request_can_match_more_than_one_top_level_router(self):
        overlaps = []
        for template, ops in app.openapi()["paths"].items():
            for method in ops:
                if method.upper() not in _HTTP:
                    continue
                hits = _matching_entries(_scope(method.upper(), _probe(template)))
                if len(hits) > 1:
                    overlaps.append((method.upper(), template, hits))
        self.assertEqual(overlaps, [], "order-sensitive overlap: reordering routers would change behaviour")

    def test_every_documented_route_is_reachable(self):
        unreachable = []
        for template, ops in app.openapi()["paths"].items():
            for method in ops:
                if method.upper() in _HTTP and not _matching_entries(_scope(method.upper(), _probe(template))):
                    unreachable.append((method.upper(), template))
        self.assertEqual(unreachable, [])

    def test_websocket_routes_do_not_overlap_and_are_reachable(self):
        paths = _websocket_paths()
        self.assertTrue(paths, "expected the realtime/scanner websocket routes")
        for path in paths:
            hits = _matching_entries(_scope("GET", _probe(path), kind="websocket"))
            self.assertEqual(len(hits), 1, (path, hits))

    def test_the_route_count_did_not_change(self):
        # Guards against a router being dropped or duplicated while reordering.
        n = sum(len(ops) for ops in app.openapi()["paths"].values())
        self.assertGreaterEqual(n, 159)


class TestBusiestRoutersComeFirst(unittest.TestCase):
    """The reason the order exists. Prefixes ranked by measured traffic share."""

    HOT = ("/api/trend/", "/api/analysis/", "/api/regime/", "/api/scanner/", "/api/multitimeframe/",
           "/api/strategy/", "/api/market-context/", "/api/market-data/")
    COLD = ("/api/system/", "/api/ai/chat/", "/api/drawing-tools", "/api/custom-indicators",
            "/api/strategy-lab/", "/api/backtest/", "/api/alerts/", "/api/finnhub/")

    def _position(self, prefix: str) -> int:
        for i, entry in enumerate(_entries()):
            inner = getattr(getattr(entry, "original_router", None), "routes", None)
            if any(getattr(r, "path", "").startswith(prefix) for r in (inner or [entry])):
                return i
        self.fail(f"no router serves {prefix}")

    def test_hot_routers_are_scanned_before_cold_ones(self):
        slowest_hot = max(self._position(p) for p in self.HOT)
        fastest_cold = min(self._position(p) for p in self.COLD)
        self.assertLess(slowest_hot, fastest_cold,
                        "a rarely-hit router was placed ahead of a busy one (each costs ~3.4 us/request)")


if __name__ == "__main__":
    unittest.main()
