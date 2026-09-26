"""
HTTP-layer tests for GET /api/trade-plan/{symbol}/draft.

Covers: query-param validation, response shape, builder integration,
and graceful handling of a builder that returns no candidates.
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from backend.api.main import app

_MINIMAL_DRAFT = {
    "symbol": "AAPL",
    "timeframe": "1d",
    "direction": "long",
    "current_price": 195.0,
    "latest_bar_timestamp": datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc).isoformat(),
    "latest_bar_data_status": "ok",
    "latest_bar_source": "alpaca",
    "bars_used": 50,
    "sources": {
        "structural": {"available": True},
        "volatility": {"available": True},
        "empirical": {"available": False, "error": "insufficient sample"},
        "options": {"available": False, "error": "skipped"},
    },
    "candidate_stops": [
        {"source": "volatility", "price": 188.0, "distance_pct": 3.6, "rationale": "ATR(14)"},
    ],
    "candidate_targets": [
        {"source": "structural", "price": 205.0, "distance_pct": 5.1, "rationale": "resistance"},
    ],
    "best_reward_risk": 1.4,
    "min_reward_risk": 1.5,
    "selected": None,
    "plan": None,
    "warnings": ["Best reward:risk (1.4) is below minimum (1.5)."],
}

_FULL_DRAFT = {
    **_MINIMAL_DRAFT,
    "best_reward_risk": 2.1,
    "min_reward_risk": 1.5,
    "selected": {
        "entry_zone_low": 193.0,
        "entry_zone_high": 196.0,
        "stop_price": 188.0,
        "stop_source": "volatility",
        "targets": [205.0],
        "target_sources": ["structural"],
    },
    "plan": {
        "entry_zone_low": 193.0,
        "entry_zone_high": 196.0,
        "stop_price": 188.0,
        "targets": [205.0],
        "risk_reward": 2.1,
        "per_share_risk": 6.5,
    },
    "warnings": [],
}


def _mock_build(return_value):
    """Patch build_draft_plan to return a pre-built dict as an object with attributes."""
    from types import SimpleNamespace

    def _to_ns(d):
        ns = SimpleNamespace(**d)
        if isinstance(d.get("selected"), dict):
            ns.selected = SimpleNamespace(**d["selected"])
        if isinstance(d.get("latest_bar_timestamp"), str):
            ns.latest_bar_timestamp = datetime.fromisoformat(d["latest_bar_timestamp"])
        for k, v in d.items():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                setattr(ns, k, [SimpleNamespace(**item) for item in v])
        return ns

    async def _fake_build(*args, **kwargs):
        return _to_ns(return_value)

    return patch("backend.api.trade_plan.router.build_draft_plan", new=_fake_build)


class TestTradePlanRouter(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=True)

    # ------------------------------------------------------------------ #
    # Query-param validation                                               #
    # ------------------------------------------------------------------ #

    def test_missing_timeframe_returns_422(self):
        r = self.client.get("/api/trade-plan/AAPL/draft?direction=long")
        self.assertEqual(r.status_code, 422)

    def test_missing_direction_returns_422(self):
        r = self.client.get("/api/trade-plan/AAPL/draft?timeframe=1d")
        self.assertEqual(r.status_code, 422)

    def test_invalid_direction_returns_422(self):
        r = self.client.get("/api/trade-plan/AAPL/draft?timeframe=1d&direction=sideways")
        self.assertEqual(r.status_code, 422)

    def test_account_value_zero_returns_422(self):
        r = self.client.get("/api/trade-plan/AAPL/draft?timeframe=1d&direction=long&account_value=0")
        self.assertEqual(r.status_code, 422)

    def test_account_value_negative_returns_422(self):
        r = self.client.get("/api/trade-plan/AAPL/draft?timeframe=1d&direction=long&account_value=-500")
        self.assertEqual(r.status_code, 422)

    def test_risk_percent_over_100_returns_422(self):
        r = self.client.get("/api/trade-plan/AAPL/draft?timeframe=1d&direction=long&risk_percent=101")
        self.assertEqual(r.status_code, 422)

    def test_min_sample_zero_returns_422(self):
        r = self.client.get("/api/trade-plan/AAPL/draft?timeframe=1d&direction=long&min_sample=0")
        self.assertEqual(r.status_code, 422)

    # ------------------------------------------------------------------ #
    # Happy-path response shape                                            #
    # ------------------------------------------------------------------ #

    def test_returns_draft_shape_with_no_selection(self):
        with _mock_build(_MINIMAL_DRAFT):
            r = self.client.get("/api/trade-plan/AAPL/draft?timeframe=1d&direction=long")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["symbol"], "AAPL")
        self.assertEqual(body["timeframe"], "1d")
        self.assertEqual(body["direction"], "long")
        self.assertIsNone(body["selected"])
        self.assertIsNone(body["plan"])
        self.assertIn("candidate_stops", body)
        self.assertIn("candidate_targets", body)
        self.assertIn("sources", body)
        self.assertIn("warnings", body)

    def test_returns_selected_and_plan_when_candidates_qualify(self):
        with _mock_build(_FULL_DRAFT):
            r = self.client.get("/api/trade-plan/AAPL/draft?timeframe=1d&direction=long")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIsNotNone(body["selected"])
        self.assertIsNotNone(body["plan"])
        sel = body["selected"]
        self.assertIn("stop_price", sel)
        self.assertIn("targets", sel)
        self.assertEqual(body["warnings"], [])

    def test_short_direction_accepted(self):
        draft = {**_FULL_DRAFT, "direction": "short"}
        with _mock_build(draft):
            r = self.client.get("/api/trade-plan/AAPL/draft?timeframe=1d&direction=short")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["direction"], "short")

    def test_symbol_is_passed_through(self):
        draft = {**_FULL_DRAFT, "symbol": "MSFT"}
        with _mock_build(draft):
            r = self.client.get("/api/trade-plan/MSFT/draft?timeframe=1h&direction=long")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["symbol"], "MSFT")

    # ------------------------------------------------------------------ #
    # Optional params forwarded to builder                                 #
    # ------------------------------------------------------------------ #

    def test_account_value_and_risk_percent_forwarded(self):
        captured = {}

        async def _fake_build(db, *, symbol, timeframe, direction,
                               account_value, risk_percent, include_options, min_sample):
            captured["account_value"] = account_value
            captured["risk_percent"] = risk_percent

            from types import SimpleNamespace
            draft = {**_FULL_DRAFT}
            ns = SimpleNamespace(**draft)
            ns.selected = SimpleNamespace(**draft["selected"])
            ns.candidate_stops = [SimpleNamespace(**s) for s in draft["candidate_stops"]]
            ns.candidate_targets = [SimpleNamespace(**s) for s in draft["candidate_targets"]]
            ns.latest_bar_timestamp = None
            return ns

        with patch("backend.api.trade_plan.router.build_draft_plan", new=_fake_build):
            r = self.client.get(
                "/api/trade-plan/AAPL/draft?timeframe=1d&direction=long"
                "&account_value=50000&risk_percent=1.5"
            )
        self.assertEqual(r.status_code, 200)
        self.assertAlmostEqual(captured["account_value"], 50000.0)
        self.assertAlmostEqual(captured["risk_percent"], 1.5)

    def test_include_options_false_forwarded(self):
        captured = {}

        async def _fake_build(db, *, symbol, timeframe, direction,
                               account_value, risk_percent, include_options, min_sample):
            captured["include_options"] = include_options
            from types import SimpleNamespace
            draft = {**_MINIMAL_DRAFT}
            ns = SimpleNamespace(**draft)
            ns.selected = None
            ns.candidate_stops = [SimpleNamespace(**s) for s in draft["candidate_stops"]]
            ns.candidate_targets = [SimpleNamespace(**s) for s in draft["candidate_targets"]]
            ns.latest_bar_timestamp = None
            return ns

        with patch("backend.api.trade_plan.router.build_draft_plan", new=_fake_build):
            r = self.client.get(
                "/api/trade-plan/AAPL/draft?timeframe=1d&direction=long&include_options=false"
            )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(captured["include_options"])


if __name__ == "__main__":
    unittest.main()
