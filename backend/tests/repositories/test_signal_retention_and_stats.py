"""
Two things about the ``historical_signals`` table:

* Retention. It had none: ~47,000 rows a day (~30 MB, ~10 GB a year), and intraday signals outlived
  the bars they were computed from by months. Each timeframe's signals are now kept for that
  timeframe's BAR retention window plus a small margin (the startup "signal hygiene" job
  regenerates signals whenever a timeframe has more bars than signals, so signals must never be
  pruned inside the bar window).
* ``get_stats``. The AI context called ``signal_recorder.get_stats(...)``, which did not exist: the
  ``AttributeError`` was swallowed and its ``historical_signal_stats`` section was empty on every
  request.
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import HistoricalSignal
from backend.repositories.signal_repository import (
    SIGNAL_RETENTION_MARGIN_DAYS,
    SignalRepository,
    prune_signals_by_retention,
)

NOW = datetime(2026, 9, 19, 12, 0)
_ING = "backend.market_data.services.ingestion_service"
_QUEUE = "backend.market_data.services.backfill_queue"
_REPO = "backend.repositories.signal_repository"


class _Db(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        HistoricalSignal.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)

    def _add(self, tf="1m", days_old=0, symbol="AAPL", state="bullish", r5=None, r10=None, n=1):
        with self.Session() as db:
            for i in range(n):
                db.add(HistoricalSignal(
                    symbol=symbol, timeframe=tf,
                    timestamp=NOW - timedelta(days=days_old, minutes=i),
                    trend_state=state, return_5b=r5, return_10b=r10,
                ))
            db.commit()

    def _count(self, **filters):
        with self.Session() as db:
            return db.query(HistoricalSignal).filter_by(**filters).count()


class TestSignalRetention(_Db):
    def _prune(self, **kw):
        with self.Session() as db:
            return prune_signals_by_retention(db, now=NOW, **kw)

    def test_intraday_signals_are_kept_for_the_bar_window_plus_the_margin(self):
        window = 16 + SIGNAL_RETENTION_MARGIN_DAYS
        self._add("1m", days_old=window - 1)         # inside: kept
        self._add("1m", days_old=window + 1)         # past the window: deleted
        self.assertEqual(self._prune(), {"1m": 1})
        self.assertEqual(self._count(timeframe="1m"), 1)

    def test_each_timeframe_uses_its_own_window(self):
        for tf, kept, dropped in (("30m", 17, 20), ("1h", 300, 380), ("4h", 300, 380),
                                  ("1d", 1000, 1110), ("1wk", 1000, 1110)):
            self._add(tf, days_old=kept)
            self._add(tf, days_old=dropped)
        deleted = self._prune()
        self.assertEqual(deleted, {tf: 1 for tf in ("30m", "1h", "4h", "1d", "1wk")})
        for tf in ("30m", "1h", "4h", "1d", "1wk"):
            self.assertEqual(self._count(timeframe=tf), 1, tf)

    def test_a_signal_is_never_pruned_inside_its_bar_retention_window(self):
        """Hygiene invariant: signals reach at least as far back as the bars they derive from."""
        from backend.config.settings import settings

        for tf in ("1m", "5m", "30m", "1h", "4h", "1d", "1wk"):
            self._add(tf, days_old=settings.retention.days_for(tf))      # exactly at the bar cutoff
        self.assertEqual(self._prune(), {})

    def test_the_margin_is_at_least_a_day(self):
        self.assertGreaterEqual(SIGNAL_RETENTION_MARGIN_DAYS, 1)

    def test_an_unknown_timeframe_falls_back_to_the_longest_window(self):
        self._add("7m", days_old=1000)
        self.assertEqual(self._prune(), {})

    def test_other_symbols_and_fresh_rows_are_untouched(self):
        self._add("1m", days_old=30, symbol="AAPL", n=3)
        self._add("1m", days_old=1, symbol="MSFT", n=3)
        self._prune()
        self.assertEqual(self._count(symbol="AAPL"), 0)
        self.assertEqual(self._count(symbol="MSFT"), 3)

    def test_chunking_still_deletes_everything_and_reports_the_total(self):
        self._add("1m", days_old=60, n=11)
        self.assertEqual(self._prune(chunk_size=3), {"1m": 11})
        self.assertEqual(self._count(), 0)

    def test_nothing_to_prune_returns_an_empty_dict(self):
        self._add("1m", days_old=1, n=5)
        self.assertEqual(self._prune(), {})

    def test_rejects_a_non_positive_chunk_size(self):
        with self.Session() as db, self.assertRaises(ValueError):
            prune_signals_by_retention(db, chunk_size=0)


class TestSignalStats(_Db):
    def _stats(self, symbol="AAPL", timeframe=None):
        with self.Session() as db:
            return SignalRepository(db).get_stats(symbol, timeframe)

    def test_no_signals(self):
        self.assertEqual(self._stats(), {"total": 0, "with_outcomes": 0, "avg_return_5b": None,
                                         "avg_return_10b": None, "win_rate": None})

    def test_win_rate_is_direction_aware(self):
        self._add(state="bullish", r5=2.0, r10=3.0)      # called it
        self._add(state="bullish", r5=-1.0, r10=-1.0)    # wrong
        self._add(state="bearish", r5=-2.0, r10=-2.0)    # called it (price fell)
        self._add(state="bearish", r5=1.0, r10=2.0)      # wrong
        stats = self._stats()
        self.assertEqual(stats["win_rate"], 0.5)
        # a naive "return > 0" rate would have said 2/4 too, so pin the case where it differs:
        self._add(state="bearish", r5=-3.0, r10=-3.0)
        self.assertEqual(self._stats()["win_rate"], 0.6)   # 3 of 5; "return > 0" would give 0.4

    def test_neutral_and_outcomeless_signals_do_not_enter_the_win_rate(self):
        self._add(state="neutral", r5=5.0, r10=5.0)
        self._add(state="bullish", r5=None)                # no outcome yet
        self._add(state="bullish", r5=1.0, r10=1.0)
        stats = self._stats()
        self.assertEqual((stats["total"], stats["with_outcomes"]), (3, 2))
        self.assertEqual(stats["win_rate"], 1.0)   # the one directional signal with an outcome won

    def test_only_neutral_signals_means_no_win_rate(self):
        self._add(state="neutral", r5=1.0, r10=1.0)
        self.assertIsNone(self._stats()["win_rate"])

    def test_average_returns(self):
        self._add(r5=1.0, r10=2.0)
        self._add(r5=3.0, r10=6.0)
        stats = self._stats()
        self.assertEqual((stats["avg_return_5b"], stats["avg_return_10b"]), (2.0, 4.0))

    def test_filters_by_symbol_case_insensitively_and_by_timeframe(self):
        self._add(tf="1d", symbol="AAPL", state="bullish", r5=1.0, r10=1.0)
        self._add(tf="1h", symbol="AAPL", state="bullish", r5=-1.0, r10=-1.0)
        self._add(tf="1d", symbol="MSFT", state="bullish", r5=-1.0, r10=-1.0)
        self.assertEqual(self._stats("aapl", "1d")["win_rate"], 1.0)
        self.assertEqual(self._stats("AAPL", "1h")["win_rate"], 0.0)
        self.assertEqual(self._stats("AAPL")["total"], 2)          # no timeframe: all of the symbol's


class TestAiContextGetsRealStats(_Db):
    """The wiring that was silently dead: build_context -> _signal_stats_context -> recorder.get_stats."""

    def test_signal_stats_context_is_populated(self):
        from backend.ai.context import _signal_stats_context

        self._add(tf="1d", state="bullish", r5=2.0, r10=4.0)
        self._add(tf="1d", state="bearish", r5=1.0, r10=1.0)
        with patch("backend.services.signal_recorder.SessionLocal", self.Session):
            ctx = _signal_stats_context("AAPL", "1d")
        self.assertEqual(ctx, {"total_signals": 2, "avg_return_5b": 1.5,
                               "avg_return_10b": 2.5, "win_rate": 0.5})

    def test_the_recorder_has_the_method_the_ai_calls(self):
        from backend.services.signal_recorder import signal_recorder

        self.assertTrue(callable(getattr(signal_recorder, "get_stats", None)))


class TestRetentionLoopWiring(unittest.IsolatedAsyncioTestCase):
    async def _one_pass(self, signal_prune, **extra):
        from unittest.mock import MagicMock

        from backend.market_data.services.ingestion_service import MarketDataIngestionService

        svc = MarketDataIngestionService(symbols=["AAPL"], timeframes=["1m"])
        svc.manager = MagicMock()

        async def stop_after_one(*_a, **_k):
            svc.is_running = False

        svc._jittered_sleep = stop_after_one
        svc.is_running = True
        with patch(f"{_ING}.SessionLocal"), \
             patch("backend.repositories.bar_repository.prune_bars_by_retention", return_value={}), \
             patch("backend.repositories.status_retention.prune_status_tables", return_value={}), \
             patch(f"{_QUEUE}.reap_orphaned_jobs", return_value=0) as reaper, \
             patch(f"{_REPO}.prune_signals_by_retention", signal_prune):
            await svc._retention_prune_loop()
        return reaper

    async def test_the_signal_prune_runs_each_pass_and_reports(self):
        from unittest.mock import MagicMock

        prune = MagicMock(return_value={"1m": 12})
        with self.assertLogs(_ING, "INFO") as cm:
            await self._one_pass(prune)
        prune.assert_called_once()
        self.assertTrue(any("Signal retention" in r.getMessage() for r in cm.records))

    async def test_a_failing_signal_prune_is_logged_and_does_not_skip_the_rest(self):
        from unittest.mock import MagicMock

        prune = MagicMock(side_effect=RuntimeError("database is locked"))
        with self.assertLogs(_ING, "ERROR") as cm:
            reaper = await self._one_pass(prune)
        reaper.assert_called_once()
        self.assertTrue(any("signal retention" in r.getMessage() for r in cm.records))


if __name__ == "__main__":
    unittest.main()
