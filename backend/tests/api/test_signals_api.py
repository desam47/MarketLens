"""
Tests for the /api/signals/* endpoints.

Covers: list, get-by-id, latest-per-symbol, regime performance,
count-by-regime, backfill trigger, and delete-old.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from backend.api.main import app  # noqa: F401
from backend.models.signal import HistoricalSignal


def _override_get_db(session_factory):
    """Return a FastAPI dependency override that uses the given session factory."""
    def _get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()
    return _get_db


class TestSignalsAPI(unittest.TestCase):

    def setUp(self):
        # Fresh per-test SQLite file. We can't use :memory: because the
        # TestClient runs in a different thread, and each connection would
        # see a different DB.
        import tempfile
        tmp = tempfile.NamedTemporaryFile(
            prefix="signals_test_", suffix=".db", delete=False
        )
        tmp.close()
        self._db_file = tmp.name
        self.engine = create_engine(
            f"sqlite:///{self._db_file}",
            connect_args={"check_same_thread": False},
        )
        # Create the full app schema, not just HistoricalSignal — the
        # regime-performance/count-by-regime endpoints also query
        # WatchlistRepository (default include_all=False), which needs the
        # `watchlists`/`watchlist_symbols` tables to exist even though this
        # test file never populates them (get_watchlists() just returns []
        # against an empty-but-present table).
        from backend.database import Base
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        # Use FastAPI's dependency_overrides — the supported way to
        # swap a Depends() target with a test-only function. The override
        # is keyed on the original callable, so the signals router's
        # `from ..dependencies import get_db` doesn't matter.
        from backend.api.dependencies import get_db as get_db_dep
        app.dependency_overrides[get_db_dep] = self._iter_session

        # Prevent alerts engine startup from hitting the DB
        self._alerts_patch = patch(
            "backend.alerts.engine.AlertsEngine.startup", return_value=None
        )
        self._alerts_patch.start()
        self.addCleanup(self._alerts_patch.stop)

        self.client = TestClient(app)
        self.addCleanup(self._cleanup_db)
        self.addCleanup(self._clear_overrides)

    def _clear_overrides(self):
        app.dependency_overrides.clear()

    def _cleanup_db(self):
        try:
            import os
            os.unlink(self._db_file)
        except OSError:
            pass

    def _iter_session(self):
        """Generator-style session for the FastAPI dependency."""
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def _clear_overrides(self):
        app.dependency_overrides.clear()

    def _seed(self, **fields):
        defaults = dict(
            symbol="AAPL",
            timestamp=datetime(2025, 1, 1),
            timeframe="1d",
            price=150.0,
            trend_state="bullish",
            market_regime="risk_on",
            return_5b=1.0,
            return_10b=2.0,
            return_20b=4.0,
            mfe=3.0,
            mae=-1.0,
            _outcome_missing=False,
        )
        defaults.update(fields)
        with self.Session() as db:
            sig = HistoricalSignal(**defaults)
            db.add(sig)
            db.commit()
            db.refresh(sig)
            return sig

    # --- list ---

    def test_list_signals_empty(self):
        r = self.client.get("/api/signals/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    def test_list_signals_returns_rows(self):
        self._seed(symbol="AAPL", timestamp=datetime(2025, 1, 1))
        self._seed(symbol="AAPL", timestamp=datetime(2025, 1, 2))
        # include_all=true: list_signals defaults to filtering by the active
        # watchlist, which this test fixture never populates — without it
        # every seeded signal is filtered out and the endpoint returns [].
        r = self.client.get("/api/signals/?include_all=true")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 2)

    def test_list_signals_filters_by_symbol(self):
        self._seed(symbol="AAPL")
        self._seed(symbol="MSFT")
        r = self.client.get("/api/signals/?symbol=AAPL&include_all=true")
        self.assertEqual(len(r.json()), 1)
        self.assertEqual(r.json()[0]["symbol"], "AAPL")

    def test_list_signals_filters_by_timeframe(self):
        self._seed(symbol="AAPL", timeframe="1d")
        self._seed(symbol="AAPL", timeframe="1h")
        r = self.client.get("/api/signals/?timeframe=1h&include_all=true")
        self.assertEqual(len(r.json()), 1)
        self.assertEqual(r.json()[0]["timeframe"], "1h")

    def test_list_signals_respects_limit(self):
        for i in range(5):
            self._seed(symbol="AAPL", timestamp=datetime(2025, 1, i + 1))
        r = self.client.get("/api/signals/?limit=3&include_all=true")
        self.assertEqual(len(r.json()), 3)

    # --- get by id ---

    def test_get_signal_by_id(self):
        sig = self._seed(symbol="MSFT", price=200.0)
        r = self.client.get(f"/api/signals/{sig.id}")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["symbol"], "MSFT")
        self.assertEqual(body["price"], 200.0)

    def test_get_signal_by_id_404(self):
        r = self.client.get("/api/signals/99999")
        self.assertEqual(r.status_code, 404)

    # --- latest per symbol ---

    def test_latest_signals_per_symbol(self):
        self._seed(symbol="AAPL", timeframe="1d", timestamp=datetime(2025, 1, 1), price=100.0)
        self._seed(symbol="AAPL", timeframe="1d", timestamp=datetime(2025, 1, 5), price=120.0)
        # Patch the router module's ingestion_service binding (the source-module
        # patch doesn't reach the router's `from ... import ingestion_service`).
        with patch("backend.api.signals.router.ingestion_service") as mock_ing:
            mock_ing.timeframes = ["1d"]
            r = self.client.get("/api/signals/symbol/AAPL/latest")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("1d", body)
        self.assertEqual(body["1d"]["price"], 120.0)

    def test_latest_signals_uppercases_symbol(self):
        self._seed(symbol="AAPL", timeframe="1d", price=100.0)
        with patch("backend.api.signals.router.ingestion_service") as mock_ing:
            mock_ing.timeframes = ["1d"]
            r = self.client.get("/api/signals/symbol/aapl/latest")
        self.assertEqual(r.status_code, 200)

    def test_latest_signals_404_when_no_data(self):
        with patch("backend.api.signals.router.ingestion_service") as mock_ing:
            mock_ing.timeframes = ["1d"]
            r = self.client.get("/api/signals/symbol/NOPE/latest")
        self.assertEqual(r.status_code, 404)

    # --- research: regime-performance ---

    def test_regime_performance(self):
        self._seed(symbol="AAPL", market_regime="risk_on", return_5b=1.0, return_10b=2.0, return_20b=4.0)
        self._seed(symbol="MSFT", market_regime="risk_on", return_5b=3.0, return_10b=4.0, return_20b=6.0)
        self._seed(symbol="GOOGL", market_regime="risk_off", return_5b=-1.0, return_10b=-2.0, return_20b=-3.0)
        r = self.client.get("/api/signals/research/regime-performance?include_all=true")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        rows = {row["regime"]: row for row in body}
        self.assertEqual(rows["risk_on"]["count"], 2)
        self.assertAlmostEqual(rows["risk_on"]["avg_return_5b"], 2.0)
        self.assertAlmostEqual(rows["risk_on"]["avg_return_20b"], 5.0)
        self.assertEqual(rows["risk_off"]["count"], 1)

    # --- research: count-by-regime ---

    def test_count_by_regime(self):
        self._seed(symbol="AAPL", market_regime="risk_on")
        self._seed(symbol="MSFT", market_regime="risk_on")
        self._seed(symbol="GOOGL", market_regime="risk_off")
        r = self.client.get("/api/signals/research/count-by-regime?include_all=true")
        self.assertEqual(r.status_code, 200)
        body = {row["regime"]: row["count"] for row in r.json()}
        self.assertEqual(body["risk_on"], 2)
        self.assertEqual(body["risk_off"], 1)

    # --- backfill trigger ---

    @patch("backend.api.signals.router.signal_recorder.backfill_outcomes", return_value=3)
    def test_backfill_endpoint(self, mock_backfill):
        r = self.client.post("/api/signals/backfill?batch_size=10")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["updated"], 3)
        mock_backfill.assert_called_once_with(batch_size=10)

    @patch("backend.api.signals.router.signal_recorder.backfill_outcomes", return_value=0)
    def test_backfill_endpoint_zero_updated(self, mock_backfill):
        r = self.client.post("/api/signals/backfill")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["updated"], 0)

    # --- record-now ---

    @patch("backend.api.signals.router.signal_recorder.record_from_recent_bars", return_value=5)
    def test_record_now_uses_ingestion_service_defaults(self, mock_record):
        # Patch the router module's ingestion_service binding (the source-module
        # patch doesn't reach the router's `from ... import ingestion_service`).
        with patch("backend.api.signals.router.ingestion_service") as mock_ing:
            mock_ing.symbols = ["AAPL", "MSFT"]
            mock_ing.timeframes = ["1d", "1h"]
            r = self.client.post("/api/signals/record")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["recorded"], 5)
        # The recorder should have been called with the symbols from ingestion service
        args, _ = mock_record.call_args
        self.assertEqual(args[0], ["AAPL", "MSFT"])
        # timeframes argument was removed (Phase 3.1 — bars table is 1m-only;
        # record_from_recent_bars queries all available timeframes)
        self.assertEqual(len(args), 1)

    # --- delete old ---

    def test_delete_old_signals(self):
        now = datetime.utcnow()
        # Insert two signals at different ages
        self._seed(symbol="AAPL", timestamp=now - timedelta(days=400))
        self._seed(symbol="MSFT", timestamp=now - timedelta(days=10))
        r = self.client.delete("/api/signals/old?older_than_days=180")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["deleted"], 1)
        self.assertEqual(body["older_than_days"], 180)
        # Verify only the recent one remains
        r2 = self.client.get("/api/signals/?include_all=true")
        remaining = r2.json()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["symbol"], "MSFT")


if __name__ == "__main__":
    unittest.main()
