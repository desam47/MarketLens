"""
Historical signals must carry the trend AT THEIR OWN BAR.

The bulk recorder used to stamp every backfilled bar with the live engine's current score, so a
whole backfill shared one label: SPY's 754 daily signals were 100% neutral and AAPL's 99.6%
bullish, 92-99% of all rows on every timeframe carried a score shared with 10+ siblings, and the
win rate built on them measured nothing. ``signal_replay`` replays the stored bars, oldest first,
through a private trend engine instead.
"""
import math
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.engines.timeframe import TimeframeEngine, multi_symbol_timeframe_engine
from backend.models.market_data_sql import BarModel
from backend.models.signal import HistoricalSignal
from backend.repositories.signal_repository import SignalRepository
from backend.services import signal_recorder as rec_mod
from backend.services.signal_recorder import SignalRecorder
from backend.services.signal_replay import (
    REPLAY_WARMUP_BARS,
    export_labels,
    label_columns,
    relabel_signals,
    replay_trend_scores,
    restore_labels,
)

_DAY0 = datetime(2024, 1, 2)


def _trading_days(n: int) -> list[datetime]:
    days, d = [], _DAY0
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _price(i: int, turn: int) -> float:
    """Rises for ``turn`` bars, then falls: a series whose trend genuinely changes."""
    base = 100 + i * 0.9 if i < turn else 100 + turn * 0.9 - (i - turn) * 1.1
    return base + math.sin(i / 3.0) * 0.8


def _bars(n: int = 450, turn: int = 260) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(timestamp=ts, close=_price(i, turn), volume=1_000_000 + (i % 7) * 50_000)
        for i, ts in enumerate(_trading_days(n))
    ]


class _Db(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        HistoricalSignal.__table__.create(self.engine)
        BarModel.__table__.create(self.engine)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.addCleanup(self.engine.dispose)

    def _store_bars(self, symbol, timeframe, bars):
        with self.Session() as db:
            db.bulk_save_objects([
                BarModel(symbol=symbol, timeframe=timeframe, timestamp=b.timestamp,
                         open=b.close, high=b.close + 0.5, low=b.close - 0.5, close=b.close,
                         volume=b.volume, provider="test", data_status="historical")
                for b in bars
            ])
            db.commit()

    def _signals(self, symbol, timeframe):
        with self.Session() as db:
            return (db.query(HistoricalSignal)
                    .filter_by(symbol=symbol, timeframe=timeframe)
                    .order_by(HistoricalSignal.timestamp.asc()).all())

    def _stamped(self, symbol="RELA", timeframe="1d", n=450, extra_orphan=True):
        bars = _bars(n)
        self._store_bars(symbol, timeframe, bars)
        with self.Session() as db:
            for i, b in enumerate(bars):
                db.add(HistoricalSignal(
                    symbol=symbol, timeframe=timeframe, timestamp=b.timestamp, price=b.close,
                    trend_score=-5.0, trend_state="neutral", strength=0.05, momentum=-5.0,
                    structure="neutral", market_regime="risk_on", strategy_version="v1",
                    data_quality="good", return_5b=0.5 + i * 0.001, return_10b=1.0, return_20b=2.0,
                    mfe=3.0, mae=-1.0))
            if extra_orphan:                        # a signal whose bar was pruned away
                db.add(HistoricalSignal(
                    symbol=symbol, timeframe=timeframe, timestamp=_DAY0 - timedelta(days=30),
                    price=90.0, trend_score=-5.0, trend_state="neutral", strength=0.05,
                    momentum=-5.0, structure="neutral", market_regime="risk_on",
                    strategy_version="v1", data_quality="good", return_5b=0.1))
            db.commit()
        return bars


class TestReplayScores(unittest.TestCase):
    def test_scores_vary_bar_to_bar_and_follow_the_trend(self):
        bars = _bars()
        scores = replay_trend_scores("REPLAYA", "1d", bars)
        labelled = [(b.timestamp, scores[b.timestamp]) for b in bars
                    if scores[b.timestamp] is not None]
        self.assertGreater(len(labelled), 200)
        self.assertGreater(len({round(s, 3) for _, s in labelled}), 100, "one stamped score")
        states = {label_columns(s)["trend_state"] for _, s in labelled}
        self.assertGreaterEqual(len(states), 2, "the series turns; the labels must too")
        # The rally's tail is called bullish, the sell-off's tail is not.
        rally_end = scores[bars[259].timestamp]
        selloff_end = scores[bars[-1].timestamp]
        self.assertGreater(rally_end, 30)
        self.assertLess(selloff_end, rally_end)

    def test_the_warm_up_bars_are_unlabelled(self):
        bars = _bars()
        scores = replay_trend_scores("REPLAYB", "1d", bars)
        self.assertEqual([b.timestamp for b in bars if scores[b.timestamp] is None][:REPLAY_WARMUP_BARS],
                         [b.timestamp for b in bars[:REPLAY_WARMUP_BARS]])
        self.assertTrue(all(scores[b.timestamp] is not None for b in bars[REPLAY_WARMUP_BARS:]))

    def test_no_look_ahead_a_bar_ignores_everything_after_it(self):
        bars = _bars()
        full = replay_trend_scores("REPLAYC", "1d", bars)
        prefix = replay_trend_scores("REPLAYC", "1d", bars[:330])
        for b in bars[:330]:
            self.assertEqual(prefix[b.timestamp], full[b.timestamp], b.timestamp)

    def test_is_repeatable(self):
        bars = _bars(300)
        self.assertEqual(replay_trend_scores("REPLAYD", "1d", bars),
                         replay_trend_scores("REPLAYD", "1d", bars))

    def test_an_unsupported_timeframe_yields_no_scores(self):
        bars = _bars(50)
        scores = replay_trend_scores("REPLAYE", "7m", bars)
        self.assertEqual(set(scores.values()), {None})
        self.assertEqual(len(scores), 50)

    def test_a_bar_the_engine_cannot_score_is_none_not_its_neighbours_score(self):
        from backend.trend.trend_engine import TrendEngine

        bars = _bars(300)
        real = TrendEngine._generate_trend_signals
        skipped = bars[250].timestamp

        def skip_one(engine, timestamp, only_timeframe=None):
            from backend.utils.timezone import ny_to_utc
            if timestamp == ny_to_utc(skipped):
                return None                       # emits nothing: the last signal is now stale
            return real(engine, timestamp, only_timeframe=only_timeframe)

        with patch.object(TrendEngine, "_generate_trend_signals", skip_one):
            scores = replay_trend_scores("REPLAYF", "1d", bars)
        self.assertIsNone(scores[skipped])
        self.assertIsNotNone(scores[bars[249].timestamp])
        self.assertIsNotNone(scores[bars[251].timestamp])


class TestReplayLeavesLiveStateAlone(unittest.TestCase):
    def test_the_live_symbols_candle_aggregator_is_untouched(self):
        live = TimeframeEngine("LIVESYM")
        live.update_tick(101.0, 500, datetime(2026, 9, 18, 15, 59))
        multi_symbol_timeframe_engine.engines["LIVESYM"] = live
        self.addCleanup(multi_symbol_timeframe_engine.engines.pop, "LIVESYM", None)
        def snapshot():
            return ({tf: len(c) for tf, c in live.candles.items()}, dict(live._last_candle_open),
                    {tf: getattr(c, "close", None) for tf, c in live.current_candles.items()})

        before = snapshot()
        replay_trend_scores("LIVESYM", "1d", _bars(260))
        self.assertIs(multi_symbol_timeframe_engine.engines["LIVESYM"], live)
        self.assertEqual(snapshot(), before)

    def test_no_scratch_aggregator_is_left_behind_even_when_the_replay_fails(self):
        replay_trend_scores("REPLAYG", "1d", _bars(60))
        with patch("backend.trend.trend_engine.TrendEngine.update", side_effect=RuntimeError("x")):
            replay_trend_scores("REPLAYG", "1d", _bars(60))     # every bar swallowed
        self.assertEqual([k for k in multi_symbol_timeframe_engine.engines
                          if k.startswith("__replay__")], [])


class TestLabelColumns(unittest.TestCase):
    def test_states_at_the_thresholds(self):
        self.assertEqual(label_columns(30.0)["trend_state"], "bullish")
        self.assertEqual(label_columns(29.99)["trend_state"], "neutral")
        self.assertEqual(label_columns(-30.0)["trend_state"], "bearish")
        self.assertEqual(label_columns(-29.99)["trend_state"], "neutral")

    def test_no_score_is_unknown_not_neutral(self):
        self.assertEqual(label_columns(None), {
            "trend_score": None, "trend_state": None, "strength": None, "momentum": None,
            "structure": None, "market_regime": None})

    def test_the_regime_is_never_stamped(self):
        self.assertIsNone(label_columns(55.0)["market_regime"])


class TestBulkRecorderUsesTheReplay(_Db):
    def setUp(self):
        super().setUp()
        self.recorder = SignalRecorder()
        patcher = patch.object(rec_mod, "SessionLocal", lambda: self.Session())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_backfilled_rows_do_not_share_the_live_engines_current_score(self):
        self._store_bars("BULKA", "1d", _bars())
        stamp = SimpleNamespace(score=-5.0)          # what the live engine reports "now"
        with patch.object(SignalRecorder, "_get_trend_signal", return_value=stamp), \
             patch.object(SignalRecorder, "_get_market_regime", return_value="risk_on"):
            self.assertEqual(self.recorder.backfill_signals_for_symbol("BULKA", timeframe="1d"), 450)

        rows = self._signals("BULKA", "1d")
        scores = [r.trend_score for r in rows if r.trend_score is not None]
        self.assertNotIn(-5.0, scores)
        self.assertGreater(len({round(s, 3) for s in scores}), 100)
        self.assertGreaterEqual(len({r.trend_state for r in rows if r.trend_state}), 2)
        self.assertEqual({r.market_regime for r in rows}, {None}, "today's regime on old bars")

    def test_the_warm_up_rows_exist_but_are_unlabelled(self):
        self._store_bars("BULKB", "1d", _bars())
        self.recorder.backfill_signals_for_symbol("BULKB", timeframe="1d")
        rows = self._signals("BULKB", "1d")
        self.assertEqual(len(rows), 450)          # one per bar: startup hygiene compares counts
        head, tail = rows[:REPLAY_WARMUP_BARS], rows[REPLAY_WARMUP_BARS:]
        self.assertEqual({(r.trend_score, r.trend_state) for r in head}, {(None, None)})
        self.assertTrue(all(r.trend_score is not None for r in tail))

    def test_a_gap_fill_labels_the_new_bars_from_the_full_history_before_them(self):
        bars = _bars()
        self._store_bars("BULKC", "1d", bars)
        self.recorder.backfill_signals_for_symbol("BULKC", timeframe="1d")
        full = {r.timestamp: r.trend_score for r in self._signals("BULKC", "1d")}

        with self.Session() as db:                  # lose the last 5 signals, keep every bar
            db.query(HistoricalSignal).filter(
                HistoricalSignal.symbol == "BULKC",
                HistoricalSignal.timestamp >= bars[-5].timestamp).delete()
            db.commit()
        self.recorder._last_recorded.clear()
        self.assertEqual(self.recorder.backfill_signals_for_symbol("BULKC", timeframe="1d"), 5)

        refilled = {r.timestamp: r.trend_score for r in self._signals("BULKC", "1d")}
        self.assertEqual(refilled, full)

    def test_nothing_to_record_does_not_replay(self):
        self._store_bars("BULKD", "1d", _bars(230))
        self.recorder.backfill_signals_for_symbol("BULKD", timeframe="1d")
        self.recorder._last_recorded.clear()
        with patch.object(rec_mod, "replay_trend_scores", side_effect=AssertionError("replayed")):
            self.assertEqual(self.recorder.backfill_signals_for_symbol("BULKD", timeframe="1d"), 0)


class TestRelabelSignals(_Db):
    """The one-off repair of rows the old recorder stamped."""

    def test_relabels_from_the_replay_and_leaves_outcomes_alone(self):
        bars = self._stamped()
        with self.Session() as db:
            stats = relabel_signals(db, "RELA", "1d")
        self.assertEqual(stats["rows"], 451)
        self.assertEqual(stats["labelled"], 450 - REPLAY_WARMUP_BARS)
        self.assertEqual(stats["unlabelled"], REPLAY_WARMUP_BARS + 1)     # warm-up + the orphan

        expected = replay_trend_scores("RELA", "1d", bars)
        rows = self._signals("RELA", "1d")
        by_ts = {r.timestamp: r for r in rows}
        for b in bars:
            r = by_ts[b.timestamp]
            self.assertEqual(r.trend_score, expected[b.timestamp])
            self.assertIsNone(r.market_regime)
            self.assertEqual(r.price, b.close)                       # untouched
            self.assertIsNotNone(r.return_5b)                        # untouched
        self.assertEqual((rows[0].trend_score, rows[0].trend_state, rows[0].market_regime),
                         (None, None, None), "the orphan has no bar to replay")
        self.assertGreaterEqual(len({r.trend_state for r in rows if r.trend_state}), 2)

    def test_second_run_changes_nothing(self):
        self._stamped()
        with self.Session() as db:
            relabel_signals(db, "RELA", "1d")
        with self.Session() as db:
            self.assertEqual(relabel_signals(db, "RELA", "1d")["changed"], 0)

    def test_dry_run_reports_but_writes_nothing(self):
        self._stamped()
        with self.Session() as db:
            stats = relabel_signals(db, "RELA", "1d", dry_run=True)
        self.assertEqual(stats["changed"], 451)
        self.assertEqual({r.trend_score for r in self._signals("RELA", "1d")}, {-5.0})

    def test_small_chunks_give_the_same_result(self):
        self._stamped()
        with self.Session() as db:
            relabel_signals(db, "RELA", "1d", chunk_size=7)
        chunked = [(r.timestamp, r.trend_score) for r in self._signals("RELA", "1d")]
        self._reset()
        self._stamped()
        with self.Session() as db:
            relabel_signals(db, "RELA", "1d")
        self.assertEqual(chunked, [(r.timestamp, r.trend_score) for r in self._signals("RELA", "1d")])

    def _reset(self):
        with self.Session() as db:
            db.query(HistoricalSignal).delete()
            db.query(BarModel).delete()
            db.commit()

    def test_only_the_named_pair_is_touched(self):
        self._stamped("RELA", "1d")
        self._stamped("RELB", "1d", extra_orphan=False)
        with self.Session() as db:
            relabel_signals(db, "RELA", "1d")
        self.assertEqual({r.trend_score for r in self._signals("RELB", "1d")}, {-5.0})

    def test_rejects_a_non_positive_chunk_size(self):
        with self.Session() as db, self.assertRaises(ValueError):
            relabel_signals(db, "RELA", "1d", chunk_size=0)

    def test_the_win_rate_exists_once_the_labels_are_real(self):
        """SPY's stored daily signals were 100% neutral, so it had no directional win rate."""
        self._stamped(extra_orphan=False)
        with self.Session() as db:
            self.assertIsNone(SignalRepository(db).get_stats("RELA", "1d")["win_rate"])
            relabel_signals(db, "RELA", "1d")
            stats = SignalRepository(db).get_stats("RELA", "1d")
        self.assertIsNotNone(stats["win_rate"])
        self.assertEqual(stats["total"], 450)


class TestLabelBackup(_Db):
    """A relabel must be undoable: the label columns are saved first and can be put back."""

    def test_export_then_restore_round_trips_the_stamped_labels(self):
        import os
        import tempfile

        self._stamped()
        with self.Session() as db:
            original = {r.id: (r.trend_score, r.trend_state, r.strength, r.momentum, r.structure,
                               r.market_regime) for r in db.query(HistoricalSignal).all()}
        fd, path = tempfile.mkstemp(suffix=".csv.gz")
        os.close(fd)
        self.addCleanup(os.unlink, path)

        with self.Session() as db:
            self.assertEqual(export_labels(db, path), 451)
            relabel_signals(db, "RELA", "1d")
            self.assertNotEqual({r.trend_score for r in db.query(HistoricalSignal).all()}, {-5.0})
            self.assertEqual(restore_labels(db, path, chunk_size=50), 451)

        with self.Session() as db:
            restored = {r.id: (r.trend_score, r.trend_state, r.strength, r.momentum, r.structure,
                               r.market_regime) for r in db.query(HistoricalSignal).all()}
        self.assertEqual(restored, original)

    def test_none_labels_survive_the_round_trip(self):
        import os
        import tempfile

        self._stamped()
        fd, path = tempfile.mkstemp(suffix=".csv.gz")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        with self.Session() as db:
            relabel_signals(db, "RELA", "1d")            # warm-up rows become all-None
            export_labels(db, path)
            db.query(HistoricalSignal).update({"trend_state": "bullish", "trend_score": 99.0})
            db.commit()
            restore_labels(db, path)
        rows = self._signals("RELA", "1d")
        self.assertEqual((rows[0].trend_score, rows[0].trend_state), (None, None))


if __name__ == "__main__":
    unittest.main()
