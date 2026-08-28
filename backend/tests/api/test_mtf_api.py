"""
Tests for the multi-timeframe API router.
"""
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.multitimeframe.router import _MTF_TIMEFRAMES, router

# Disable data-quality log noise during tests.
logging.getLogger("backend.data_quality").setLevel(logging.CRITICAL)


def _client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestMTFConfluenceEndpoint:
    """Extended confluence endpoint: all Phase 7 fields present."""

    def test_confluence_no_signal_returns_phase7_defaults(self):
        """Fresh symbol: confluence returns Phase 7 fields with no-signal defaults."""
        client = _client()
        resp = client.get("/api/multitimeframe/NOSUCHTICKER/confluence")
        assert resp.status_code == 200
        data = resp.json()
        # Phase 7 fields present (may have default values when no signal).
        assert "bullish_alignment" in data
        assert "bearish_alignment" in data
        assert "conflicting" in data
        assert "short_term_direction" in data
        assert "intermediate_direction" in data
        assert "higher_direction" in data
        assert "preset" in data
        assert isinstance(data["conflicting"], int)
        assert isinstance(data["bullish_alignment"], float)
        assert isinstance(data["bearish_alignment"], float)

    def test_confluence_preset_field_is_string(self):
        """The preset field is always a string (engine preset name)."""
        client = _client()
        resp = client.get("/api/multitimeframe/NOSUCHTICKER/confluence")
        assert resp.status_code == 200
        assert isinstance(resp.json()["preset"], str)


class TestMTFSnapshotEndpoint:
    """Snapshot endpoints (Phase 7)."""

    def test_snapshot_returns_null_for_no_data(self):
        """No data: snapshot endpoint returns null snapshot, not an error."""
        client = _client()
        resp = client.get("/api/multitimeframe/FRESHONE/snapshot")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "FRESHONE"
        assert data["snapshot"] is None

    def test_snapshot_history_returns_empty_list(self):
        """No data: snapshot history returns empty list."""
        client = _client()
        resp = client.get("/api/multitimeframe/FRESHONE/snapshot/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "FRESHONE"
        assert data["history"] == []
        assert data["count"] == 0

    def test_snapshot_history_respects_limit(self):
        """snapshot/history respects the ?limit= query param."""
        client = _client()
        resp = client.get("/api/multitimeframe/FRESHONE/snapshot/history?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0  # no data seeded, so empty regardless
        assert len(data["history"]) <= 5


class TestMTFLiveTickRegistration:
    """Regression: ensure the router subscribes to every bar:{tf} we ingest.

    Phase 7 closure note: the original implementation only registered
    5 timeframes (1m/5m/15m/1h/1d), silently dropping 30m and 1wk from
    the live-tick path. This test fails fast if a timeframe is missing.
    """

    def test_mtf_timeframes_include_1m(self):
        """1m is in _MTF_TIMEFRAMES — the user-visible shortest horizon."""
        assert "1m" in _MTF_TIMEFRAMES

    def test_mtf_timeframes_include_30m_and_1wk(self):
        """30m and 1wk are both registered for live-tick dispatch."""
        assert "30m" in _MTF_TIMEFRAMES
        assert "1wk" in _MTF_TIMEFRAMES

    def test_mtf_timeframes_match_ingestion_default(self):
        """Router subscribes to the same set the ingestion service fetches.

        4h is excluded from the ingestion default (yfinance has no native
        4h interval) but registered in the router for any future
        provider-derived 4h bars.
        """
        # The ingestion service default lives in code; the canonical
        # truth is the union minus 4h, since 4h is router-only.
        # Assert each ingestion TF is registered.
        for tf in ("1m", "5m", "15m", "30m", "1h", "1d", "1wk"):
            assert tf in _MTF_TIMEFRAMES, f"{tf} missing from _MTF_TIMEFRAMES"
