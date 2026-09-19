"""
The inline fallbacks behind the volume / breakout / breakdown alert conditions.

``_compute_avg_volume`` / ``_compute_highest_high`` / ``_compute_lowest_low`` used
``func.avg/max/min(...).order_by(...).limit(...)``. An aggregate returns ONE row, so the
ORDER BY / LIMIT never restricted which rows it read: "highest high over the last 20
bars" was the highest high in the entire stored history, and "average volume" was the
mean of every bar for the symbol with all timeframes mixed together. They only run when
a caller omits the pre-computed value (payloads.py computes it correctly), which is why
it went unnoticed — and there were no tests.
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.alerts.conditions import evaluators, helpers
from backend.models.market_data_sql import BarModel

T0 = datetime(2026, 1, 1)


class _DbCase(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        BarModel.__table__.create(engine)
        self.Session = sessionmaker(bind=engine)
        patcher = patch.object(helpers, "SessionLocal", self.Session)   # never touch the real DB
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(engine.dispose)

    def add_bars(self, symbol, timeframe, rows):
        """rows: (high, low, volume) oldest -> newest, one bar per day."""
        with self.Session() as db:
            for i, (high, low, volume) in enumerate(rows):
                db.add(BarModel(symbol=symbol, timeframe=timeframe, open=low, high=high, low=low,
                                close=high, volume=volume, timestamp=T0 + timedelta(days=i),
                                provider="t", data_status="HISTORICAL", source="raw", session="regular"))
            db.commit()


class TestLookbackIsActuallyRespected(_DbCase):
    def test_highest_high_is_over_the_last_n_bars_not_the_whole_history(self):
        # An ancient spike of 1000, then 30 ordinary bars topping out at 110, then the "current" bar.
        rows = [(1000, 900, 10)] + [(100 + (i % 11), 99, 10) for i in range(30)] + [(999, 998, 10)]
        self.add_bars("AAPL", "1d", rows)
        result = helpers._compute_highest_high("AAPL", "1d", 20)
        self.assertEqual(result, 110)   # not 1000 (history) and not 999 (the current bar)

    def test_lowest_low_is_over_the_last_n_bars_not_the_whole_history(self):
        rows = [(50, 1, 10)] + [(100, 90 + (i % 5), 10) for i in range(30)] + [(100, 2, 10)]
        self.add_bars("AAPL", "1d", rows)
        self.assertEqual(helpers._compute_lowest_low("AAPL", "1d", 20), 90)   # not 1, not 2

    def test_the_current_bar_is_excluded(self):
        self.add_bars("AAPL", "1d", [(10, 5, 100)] * 5 + [(500, 1, 999_999)])
        self.assertEqual(helpers._compute_highest_high("AAPL", "1d", 5), 10)
        self.assertEqual(helpers._compute_lowest_low("AAPL", "1d", 5), 5)
        self.assertEqual(helpers._compute_avg_volume("AAPL", 5, "1d"), 100)

    def test_only_the_lookback_window_counts(self):
        vols = [1_000_000] * 10 + [100] * 20 + [50]            # old huge bars, then 20 small, then current
        self.add_bars("AAPL", "1d", [(10, 5, v) for v in vols])
        self.assertEqual(helpers._compute_avg_volume("AAPL", 20, "1d"), 100)

    def test_average_volume_does_not_mix_timeframes(self):
        self.add_bars("AAPL", "1d", [(10, 5, 5_000_000)] * 22)
        self.add_bars("AAPL", "1m", [(10, 5, 1_000)] * 22)
        self.assertEqual(helpers._compute_avg_volume("AAPL", 20, "1d"), 5_000_000)
        self.assertEqual(helpers._compute_avg_volume("AAPL", 20, "1m"), 1_000)

    def test_other_symbols_and_timeframes_are_ignored(self):
        self.add_bars("MSFT", "1d", [(9999, 1, 1)] * 25)
        self.add_bars("AAPL", "5m", [(9999, 1, 1)] * 25)
        self.add_bars("AAPL", "1d", [(10, 5, 100)] * 25)
        self.assertEqual(helpers._compute_highest_high("AAPL", "1d", 20), 10)

    def test_no_or_insufficient_data_returns_none(self):
        self.assertIsNone(helpers._compute_highest_high("NOPE", "1d", 20))
        self.assertIsNone(helpers._compute_lowest_low("NOPE", "1d", 20))
        self.assertIsNone(helpers._compute_avg_volume("NOPE", 20, "1d"))
        self.add_bars("ONE", "1d", [(10, 5, 100)])              # only the "current" bar, no history
        self.assertIsNone(helpers._compute_highest_high("ONE", "1d", 20))


class TestEvaluatorsUseTheCorrectFallback(_DbCase):
    """The user-visible symptom: an alert evaluated without pre-computed values."""

    def test_breakout_fires_when_price_beats_the_recent_high_not_the_all_time_high(self):
        rows = [(1000, 900, 10)] + [(100 + (i % 11), 99, 10) for i in range(30)] + [(105, 104, 10)]
        self.add_bars("AAPL", "1d", rows)
        self.assertTrue(evaluators._eval_breakout("20", {"current_price": 111.0, "symbol": "AAPL", "timeframe": "1d"}))
        self.assertFalse(evaluators._eval_breakout("20", {"current_price": 109.0, "symbol": "AAPL", "timeframe": "1d"}))

    def test_breakdown_fires_below_the_recent_low(self):
        rows = [(50, 1, 10)] + [(100, 90 + (i % 5), 10) for i in range(30)] + [(95, 94, 10)]
        self.add_bars("AAPL", "1d", rows)
        self.assertTrue(evaluators._eval_breakdown("20", {"current_price": 89.0, "symbol": "AAPL", "timeframe": "1d"}))
        self.assertFalse(evaluators._eval_breakdown("20", {"current_price": 91.0, "symbol": "AAPL", "timeframe": "1d"}))

    def test_volume_spike_compares_against_the_same_timeframes_average(self):
        self.add_bars("AAPL", "1d", [(10, 5, 1_000)] * 22)
        self.add_bars("AAPL", "1m", [(10, 5, 500_000)] * 22)     # would drag a mixed average way up
        spike = evaluators._eval_volume_spike if hasattr(evaluators, "_eval_volume_spike") else None
        fn = spike or next(getattr(evaluators, n) for n in dir(evaluators) if n.startswith("_eval_volume"))
        self.assertTrue(fn("2.0", {"current_volume": 2_500, "symbol": "AAPL", "timeframe": "1d"}))
        self.assertFalse(fn("2.0", {"current_volume": 1_500, "symbol": "AAPL", "timeframe": "1d"}))


class TestDatabaseFailuresAreVisible(unittest.TestCase):
    def test_a_failing_query_still_degrades_to_no_data_but_logs_a_warning(self):
        def boom():
            raise RuntimeError("db is down")

        with patch.object(helpers, "SessionLocal", boom):
            with self.assertLogs("backend.alerts.conditions.helpers", level="WARNING") as logs:
                self.assertEqual(helpers._get_recent_bars("AAPL", "1d", 5), [])
                self.assertIsNone(helpers._compute_highest_high("AAPL", "1d", 5))
        self.assertTrue(any("could not load AAPL/1d bars" in m for m in logs.output))


if __name__ == "__main__":
    unittest.main()
