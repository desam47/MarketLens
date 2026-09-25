"""
Tests for GET /api/signals/research/excursions.

Covers: a sufficient slice returning percentiles, a thin slice withholding
them while offering a relaxation ladder, direction mapping (long/short ->
bullish/bearish), strength-band filtering, and parameter validation.
"""

import os
import sys
import tempfile
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
    def _get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    return _get_db


class TestSignalExcursionsAPI(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(prefix="excursions_test_", suffix=".db", delete=False)
        tmp.close()
        self._db_file = tmp.name
        self.engine = create_engine(
            f"sqlite:///{self._db_file}", connect_args={"check_same_thread": False}
        )
        from backend.database import Base

        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        from backend.api.dependencies import get_db as get_db_dep

        app.dependency_overrides[get_db_dep] = _override_get_db(self.Session)

        self._alerts_patch = patch(
            "backend.alerts.engine.AlertsEngine.startup", return_value=None
        )
        self._alerts_patch.start()
        self.addCleanup(self._alerts_patch.stop)

        self.client = TestClient(app)
        self._seed_cursor = 0
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        app.dependency_overrides.clear()
        try:
            os.unlink(self._db_file)
        except OSError:
            pass

    def _seed(self, *, symbol, timeframe, trend_state, count, mae, mfe, r=1.0, strength=0.5):
        """Insert outcome-complete signals.

        Rows are unique on (symbol, timeframe, timestamp), so each call takes a
        fresh hour range from a shared cursor -- several seeds can target the
        same symbol/timeframe without colliding.
        """
        base = datetime(2026, 1, 15, 12, 0, 0) + timedelta(hours=self._seed_cursor)
        self._seed_cursor += count
        db = self.Session()
        try:
            for i in range(count):
                db.add(
                    HistoricalSignal(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=base + timedelta(hours=i),
                        price=100.0,
                        trend_state=trend_state,
                        trend_score=10.0,
                        strength=strength,
                        mae=mae,
                        mfe=mfe,
                        return_5b=r,
                        return_10b=r,
                        return_20b=r,
                    )
                )
            db.commit()
        finally:
            db.close()

    def _get(self, **params):
        return self.client.get("/api/signals/research/excursions", params=params)

    def test_sufficient_slice_returns_percentiles(self):
        self._seed(
            symbol="SPY", timeframe="5m", trend_state="bullish",
            count=150, mae=-1.0, mfe=2.0, r=1.0,
        )
        resp = self._get(symbol="SPY", timeframe="5m", direction="long")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()

        self.assertEqual(body["sample_size"], 150)
        self.assertTrue(body["sufficient"])
        self.assertEqual(body["confidence"], "moderate")
        self.assertEqual(body["filters"]["trend_state"], "bullish")
        # Adverse is a positive magnitude of the stored negative mae.
        self.assertEqual(body["adverse_excursion_pct"]["p75"], 1.0)
        self.assertEqual(body["favorable_excursion_pct"]["p50"], 2.0)
        self.assertEqual(body["win_rate"], 1.0)
        self.assertEqual(body["relaxation"], [])

    def test_short_direction_maps_to_bearish_and_swaps_excursions(self):
        # Raw mae -4 / mfe +1: for a short, adverse is the +1 high-side move
        # and favorable is the 4-point fall.
        self._seed(
            symbol="SPY", timeframe="5m", trend_state="bearish",
            count=150, mae=-4.0, mfe=1.0, r=-3.0,
        )
        body = self._get(symbol="SPY", timeframe="5m", direction="short").json()

        self.assertEqual(body["filters"]["trend_state"], "bearish")
        self.assertEqual(body["adverse_excursion_pct"]["p50"], 1.0)
        self.assertEqual(body["favorable_excursion_pct"]["p50"], 4.0)
        # A 3-point fall is a win for a short.
        self.assertEqual(body["win_rate"], 1.0)

    def test_direction_isolates_trend_state(self):
        self._seed(symbol="SPY", timeframe="5m", trend_state="bullish", count=150, mae=-1.0, mfe=2.0)
        self._seed(symbol="SPY", timeframe="5m", trend_state="bearish", count=150, mae=-9.0, mfe=9.0)

        longs = self._get(symbol="SPY", timeframe="5m", direction="long").json()
        self.assertEqual(longs["sample_size"], 150)
        self.assertEqual(longs["adverse_excursion_pct"]["p50"], 1.0)

    def test_thin_slice_withholds_stats_and_offers_all_symbols_rung(self):
        self._seed(symbol="TSLA", timeframe="5m", trend_state="bullish", count=20, mae=-2.0, mfe=3.0)
        self._seed(symbol="SPY", timeframe="5m", trend_state="bullish", count=200, mae=-1.0, mfe=2.0)

        body = self._get(symbol="TSLA", timeframe="5m", direction="long").json()

        self.assertEqual(body["sample_size"], 20)
        self.assertFalse(body["sufficient"])
        self.assertEqual(body["confidence"], "insufficient")
        # Relaxed numbers must never leak into the primary fields.
        self.assertIsNone(body["adverse_excursion_pct"])
        self.assertIsNone(body["win_rate"])

        rungs = body["relaxation"]
        self.assertTrue(rungs)
        final = rungs[-1]
        self.assertEqual(final["level"], "all_symbols")
        self.assertTrue(final["sufficient"])
        self.assertIsNone(final["filters"]["symbol"])
        self.assertEqual(final["sample_size"], 220)
        self.assertIn("all symbols", final["label"])

    def test_strength_band_rung_tried_before_pooling_symbols(self):
        # Only 20 rows in the requested band, but 200 outside it for the
        # same symbol: dropping the band should suffice without pooling.
        self._seed(
            symbol="SPY", timeframe="5m", trend_state="bullish",
            count=20, mae=-2.0, mfe=3.0, strength=0.9,
        )
        self._seed(
            symbol="SPY", timeframe="5m", trend_state="bullish",
            count=200, mae=-1.0, mfe=2.0, strength=0.2,
        )

        body = self._get(
            symbol="SPY", timeframe="5m", direction="long", strength_min=0.8
        ).json()

        self.assertEqual(body["sample_size"], 20)
        self.assertFalse(body["sufficient"])
        rungs = body["relaxation"]
        self.assertEqual(rungs[0]["level"], "no_strength_band")
        self.assertTrue(rungs[0]["sufficient"])
        self.assertEqual(rungs[0]["sample_size"], 220)
        self.assertIsNone(rungs[0]["filters"]["strength_min"])
        # Ladder stops at the first sufficient rung.
        self.assertEqual(len(rungs), 1)

    def test_strength_band_filters_the_primary_slice(self):
        self._seed(
            symbol="SPY", timeframe="5m", trend_state="bullish",
            count=150, mae=-5.0, mfe=5.0, strength=0.9,
        )
        self._seed(
            symbol="SPY", timeframe="5m", trend_state="bullish",
            count=150, mae=-1.0, mfe=1.0, strength=0.1,
        )

        body = self._get(
            symbol="SPY", timeframe="5m", direction="long", strength_min=0.8
        ).json()

        self.assertEqual(body["sample_size"], 150)
        self.assertEqual(body["adverse_excursion_pct"]["p50"], 5.0)

    def test_min_sample_override(self):
        self._seed(symbol="SPY", timeframe="5m", trend_state="bullish", count=20, mae=-1.0, mfe=2.0)
        body = self._get(symbol="SPY", timeframe="5m", direction="long", min_sample=10).json()

        self.assertTrue(body["sufficient"])
        self.assertEqual(body["min_sample"], 10)
        self.assertEqual(body["adverse_excursion_pct"]["p50"], 1.0)

    def test_empty_database_is_insufficient_not_an_error(self):
        resp = self._get(symbol="SPY", timeframe="5m", direction="long")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["sample_size"], 0)
        self.assertFalse(body["sufficient"])
        self.assertIsNone(body["adverse_excursion_pct"])

    def test_incomplete_outcomes_excluded(self):
        self._seed(symbol="SPY", timeframe="5m", trend_state="bullish", count=150, mae=-1.0, mfe=2.0)
        db = self.Session()
        try:
            for i in range(100):
                db.add(
                    HistoricalSignal(
                        symbol="SPY", timeframe="5m",
                        timestamp=datetime(2026, 2, 1) + timedelta(hours=i),
                        price=100.0, trend_state="bullish", strength=0.5,
                        mae=-50.0, mfe=50.0, return_5b=1.0,  # return_10b/20b missing
                    )
                )
            db.commit()
        finally:
            db.close()

        body = self._get(symbol="SPY", timeframe="5m", direction="long").json()
        self.assertEqual(body["sample_size"], 150)
        self.assertEqual(body["adverse_excursion_pct"]["p50"], 1.0)

    def test_invalid_direction_rejected(self):
        self.assertEqual(
            self._get(symbol="SPY", timeframe="5m", direction="sideways").status_code, 422
        )

    def test_inverted_strength_band_rejected(self):
        resp = self._get(
            symbol="SPY", timeframe="5m", direction="long",
            strength_min=0.9, strength_max=0.2,
        )
        self.assertEqual(resp.status_code, 422)

    def test_symbol_and_timeframe_required(self):
        self.assertEqual(self._get(timeframe="5m", direction="long").status_code, 422)
        self.assertEqual(self._get(symbol="SPY", direction="long").status_code, 422)


if __name__ == "__main__":
    unittest.main()
