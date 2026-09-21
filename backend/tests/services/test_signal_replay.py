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
    _CANDLES_KEPT,
    _VALUES_KEPT,
    REPLAY_WARMUP_BARS,
    BarReplay,
    bar_end,
    export_labels,
    is_closed,
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
            db.bulk_save_objects(
                [
                    BarModel(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=b.timestamp,
                        open=b.close,
                        high=b.close + 0.5,
                        low=b.close - 0.5,
                        close=b.close,
                        volume=b.volume,
                        provider="test",
                        data_status="historical",
                    )
                    for b in bars
                ]
            )
            db.commit()

    def _signals(self, symbol, timeframe):
        with self.Session() as db:
            return (
                db.query(HistoricalSignal)
                .filter_by(symbol=symbol, timeframe=timeframe)
                .order_by(HistoricalSignal.timestamp.asc())
                .all()
            )

    def _stamped(self, symbol="RELA", timeframe="1d", n=450, extra_orphan=True):
        bars = _bars(n)
        self._store_bars(symbol, timeframe, bars)
        with self.Session() as db:
            for i, b in enumerate(bars):
                db.add(
                    HistoricalSignal(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=b.timestamp,
                        price=b.close,
                        trend_score=-5.0,
                        trend_state="neutral",
                        strength=0.05,
                        momentum=-5.0,
                        structure="neutral",
                        market_regime="risk_on",
                        strategy_version="v1",
                        data_quality="good",
                        return_5b=0.5 + i * 0.001,
                        return_10b=1.0,
                        return_20b=2.0,
                        mfe=3.0,
                        mae=-1.0,
                    )
                )
            if extra_orphan:  # a signal whose bar was pruned away
                db.add(
                    HistoricalSignal(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=_DAY0 - timedelta(days=30),
                        price=90.0,
                        trend_score=-5.0,
                        trend_state="neutral",
                        strength=0.05,
                        momentum=-5.0,
                        structure="neutral",
                        market_regime="risk_on",
                        strategy_version="v1",
                        data_quality="good",
                        return_5b=0.1,
                    )
                )
            db.commit()
        return bars


class TestReplayScores(unittest.TestCase):
    def test_scores_vary_bar_to_bar_and_follow_the_trend(self):
        bars = _bars()
        scores = replay_trend_scores("REPLAYA", "1d", bars)
        labelled = [
            (b.timestamp, scores[b.timestamp]) for b in bars if scores[b.timestamp] is not None
        ]
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
        self.assertEqual(
            [b.timestamp for b in bars if scores[b.timestamp] is None][:REPLAY_WARMUP_BARS],
            [b.timestamp for b in bars[:REPLAY_WARMUP_BARS]],
        )
        self.assertTrue(all(scores[b.timestamp] is not None for b in bars[REPLAY_WARMUP_BARS:]))

    def test_no_look_ahead_a_bar_ignores_everything_after_it(self):
        bars = _bars()
        full = replay_trend_scores("REPLAYC", "1d", bars)
        prefix = replay_trend_scores("REPLAYC", "1d", bars[:330])
        for b in bars[:330]:
            self.assertEqual(prefix[b.timestamp], full[b.timestamp], b.timestamp)

    def test_is_repeatable(self):
        bars = _bars(300)
        self.assertEqual(
            replay_trend_scores("REPLAYD", "1d", bars), replay_trend_scores("REPLAYD", "1d", bars)
        )

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
                return None  # emits nothing: the last signal is now stale
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
            return (
                {tf: len(c) for tf, c in live.candles.items()},
                dict(live._last_candle_open),
                {tf: getattr(c, "close", None) for tf, c in live.current_candles.items()},
            )

        before = snapshot()
        replay_trend_scores("LIVESYM", "1d", _bars(260))
        self.assertIs(multi_symbol_timeframe_engine.engines["LIVESYM"], live)
        self.assertEqual(snapshot(), before)

    def test_no_scratch_aggregator_is_left_behind_even_when_the_replay_fails(self):
        replay_trend_scores("REPLAYG", "1d", _bars(60))
        with patch("backend.trend.trend_engine.TrendEngine.update", side_effect=RuntimeError("x")):
            replay_trend_scores("REPLAYG", "1d", _bars(60))  # every bar swallowed
        self.assertEqual(
            [k for k in multi_symbol_timeframe_engine.engines if k.startswith("__replay__")], []
        )


class TestLabelColumns(unittest.TestCase):
    def test_states_at_the_thresholds(self):
        self.assertEqual(label_columns(30.0)["trend_state"], "bullish")
        self.assertEqual(label_columns(29.99)["trend_state"], "neutral")
        self.assertEqual(label_columns(-30.0)["trend_state"], "bearish")
        self.assertEqual(label_columns(-29.99)["trend_state"], "neutral")

    def test_no_score_is_unknown_not_neutral(self):
        self.assertEqual(
            label_columns(None),
            {
                "trend_score": None,
                "trend_state": None,
                "strength": None,
                "momentum": None,
                "structure": None,
                "market_regime": None,
            },
        )

    def test_the_regime_is_never_stamped(self):
        self.assertIsNone(label_columns(55.0)["market_regime"])


class TestBulkRecorderUsesTheReplay(_Db):
    def setUp(self):
        super().setUp()
        self.recorder = SignalRecorder()
        patcher = patch.object(rec_mod, "SessionLocal", lambda: self.Session())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_backfilled_rows_carry_their_own_replayed_score(self):
        bars = _bars()
        self._store_bars("BULKA", "1d", bars)
        with patch.object(SignalRecorder, "_get_market_regime", return_value="risk_on"):
            self.assertEqual(
                self.recorder.backfill_signals_for_symbol("BULKA", timeframe="1d"), 450
            )

        rows = self._signals("BULKA", "1d")
        expected = replay_trend_scores("BULKA", "1d", bars)
        self.assertEqual({r.timestamp: r.trend_score for r in rows}, expected)
        scores = [r.trend_score for r in rows if r.trend_score is not None]
        self.assertGreater(len({round(s, 3) for s in scores}), 100, "one stamped score")
        self.assertGreaterEqual(len({r.trend_state for r in rows if r.trend_state}), 2)
        self.assertEqual({r.market_regime for r in rows}, {None}, "today's regime on old bars")

    def test_the_warm_up_rows_exist_but_are_unlabelled(self):
        self._store_bars("BULKB", "1d", _bars())
        self.recorder.backfill_signals_for_symbol("BULKB", timeframe="1d")
        rows = self._signals("BULKB", "1d")
        self.assertEqual(len(rows), 450)  # one per bar: startup hygiene compares counts
        head, tail = rows[:REPLAY_WARMUP_BARS], rows[REPLAY_WARMUP_BARS:]
        self.assertEqual({(r.trend_score, r.trend_state) for r in head}, {(None, None)})
        self.assertTrue(all(r.trend_score is not None for r in tail))

    def test_a_gap_fill_labels_the_new_bars_from_the_full_history_before_them(self):
        bars = _bars()
        self._store_bars("BULKC", "1d", bars)
        self.recorder.backfill_signals_for_symbol("BULKC", timeframe="1d")
        full = {r.timestamp: r.trend_score for r in self._signals("BULKC", "1d")}

        with self.Session() as db:  # lose the last 5 signals, keep every bar
            db.query(HistoricalSignal).filter(
                HistoricalSignal.symbol == "BULKC", HistoricalSignal.timestamp >= bars[-5].timestamp
            ).delete()
            db.commit()
        self.recorder._last_recorded.clear()
        self.assertEqual(self.recorder.backfill_signals_for_symbol("BULKC", timeframe="1d"), 5)

        refilled = {r.timestamp: r.trend_score for r in self._signals("BULKC", "1d")}
        self.assertEqual(refilled, full)

    def test_a_second_pass_records_nothing(self):
        self._store_bars("BULKD", "1d", _bars(230))
        self.assertEqual(self.recorder.backfill_signals_for_symbol("BULKD", timeframe="1d"), 230)
        self.assertEqual(self.recorder.backfill_signals_for_symbol("BULKD", timeframe="1d"), 0)


def _hourly_bars(n: int = 300) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            timestamp=datetime(2024, 1, 2, 8) + timedelta(hours=i),
            close=_price(i, 200),
            volume=1_000_000 + (i % 5) * 20_000,
        )
        for i in range(n)
    ]


def _daily_close(bar) -> datetime:
    return bar_end(bar.timestamp, "1d")


class TestClosedBars(unittest.TestCase):
    def test_a_candle_closes_one_timeframe_after_its_open_time(self):
        t = datetime(2026, 9, 18, 10, 0)
        for tf, minutes in (("1m", 1), ("5m", 5), ("30m", 30), ("1h", 60), ("4h", 240)):
            self.assertEqual(bar_end(t, tf), t + timedelta(minutes=minutes), tf)
        self.assertEqual(bar_end(datetime(2026, 9, 18), "1d"), datetime(2026, 9, 19))
        self.assertEqual(bar_end(datetime(2026, 9, 14), "1wk"), datetime(2026, 9, 21))

    def test_closed_exactly_at_its_end_and_not_a_second_before(self):
        t = datetime(2026, 9, 18, 10, 0)
        self.assertFalse(is_closed(t, "1m", datetime(2026, 9, 18, 10, 0, 59)))
        self.assertTrue(is_closed(t, "1m", datetime(2026, 9, 18, 10, 1)))

    def test_an_unknown_timeframe_is_never_held_back(self):
        self.assertTrue(is_closed(datetime(2026, 9, 18, 10, 0), "7m", datetime(2026, 9, 18, 10, 0)))


class TestBarReplayIsBoundedAndIncremental(unittest.TestCase):
    def test_the_candle_history_does_not_grow_with_the_bars_fed(self):
        replay = BarReplay("BOUNDED", "1d")
        for bar in _bars(450):
            replay.feed(bar)
        kept = max(len(c) for c in replay._engine.timeframe_engine.candles.values())
        self.assertLessEqual(kept, _CANDLES_KEPT)

    def test_the_indicator_histories_stay_bounded_and_only_the_target_stack_is_kept(self):
        replay = BarReplay("BOUNDED2", "1d")
        for bar in _bars(900):
            replay.feed(bar)
        stacks = replay._engine.indicators
        self.assertEqual(list(stacks), [replay._tf])
        for indicator in stacks[replay._tf].values():
            self.assertLessEqual(len(indicator.values), _VALUES_KEPT)
            self.assertLessEqual(len(indicator.timestamps), _VALUES_KEPT)

    def test_trimming_and_pruning_change_no_score(self):
        """Compared with an engine left exactly as the live seeder builds it."""
        from backend.engines.timeframe import (
            Timeframe,
            TimeframeEngine,
            multi_symbol_timeframe_engine,
        )
        from backend.trend.trend_engine import TrendEngine
        from backend.utils.timezone import ny_to_utc

        bars = _bars(900)
        replay = BarReplay("PLAINA", "1d")
        got = [replay.feed(b) for b in bars]

        multi_symbol_timeframe_engine.engines["__plain__"] = TimeframeEngine("__plain__")
        try:
            engine = TrendEngine("__plain__")
        finally:
            multi_symbol_timeframe_engine.engines.pop("__plain__", None)
        want = []
        for i, b in enumerate(bars):
            engine.update(
                price=b.close,
                volume=int(b.volume),
                timestamp=b.timestamp,
                only_timeframe=Timeframe.ONE_DAY,
            )
            sig = engine.get_current_trend(Timeframe.ONE_DAY)
            ok = (
                sig is not None
                and sig.score is not None
                and i >= REPLAY_WARMUP_BARS
                and sig.timestamp == ny_to_utc(b.timestamp)
            )
            want.append(float(sig.score) if ok else None)
        self.assertEqual(got, want)

    def test_feeding_bar_by_bar_equals_replaying_the_lot(self):
        bars = _bars(350)
        replay = BarReplay("STEPPED", "1d")
        stepped = {b.timestamp: replay.feed(b) for b in bars}
        self.assertEqual(stepped, replay_trend_scores("STEPPED", "1d", bars))
        self.assertEqual(replay.last_ts, bars[-1].timestamp)


class TestLiveRecording(_Db):
    """Bars are recorded as they CLOSE, from the same replay that labels a backfill."""

    def setUp(self):
        super().setUp()
        self.recorder = SignalRecorder()
        patcher = patch.object(rec_mod, "SessionLocal", lambda: self.Session())
        patcher.start()
        self.addCleanup(patcher.stop)
        regime = patch.object(SignalRecorder, "_get_market_regime", return_value="risk_on")
        regime.start()
        self.addCleanup(regime.stop)

    def _scores(self, symbol, timeframe="1d"):
        return {r.timestamp: r.trend_score for r in self._signals(symbol, timeframe)}

    def test_every_closed_bar_gets_the_replayed_score(self):
        bars = _bars(300)
        self._store_bars("LIVEA", "1d", bars)
        n = self.recorder.record_from_recent_bars(
            ["LIVEA"], now=_daily_close(bars[-1]) + timedelta(hours=1)
        )
        self.assertEqual(n, 300)
        self.assertEqual(self._scores("LIVEA"), replay_trend_scores("LIVEA", "1d", bars))

    def test_a_forming_bar_is_held_back_until_it_closes(self):
        bars = _bars(300)
        self._store_bars("LIVEB", "1d", bars)
        mid_bar = bars[-1].timestamp + timedelta(hours=6)
        self.assertEqual(self.recorder.record_from_recent_bars(["LIVEB"], now=mid_bar), 299)
        self.assertNotIn(bars[-1].timestamp, self._scores("LIVEB"))

        self.assertEqual(self.recorder.record_from_recent_bars(["LIVEB"], now=mid_bar), 0)
        after = _daily_close(bars[-1]) + timedelta(minutes=1)
        self.assertEqual(self.recorder.record_from_recent_bars(["LIVEB"], now=after), 1)
        self.assertEqual(self._scores("LIVEB"), replay_trend_scores("LIVEB", "1d", bars))

    def test_the_forming_bar_never_reaches_the_engine(self):
        """Its close moves while it forms; a tick the engine has seen cannot be taken back."""
        bars = _bars(300)
        self._store_bars("LIVEC", "1d", bars)
        self.recorder.record_from_recent_bars(
            ["LIVEC"], now=bars[-1].timestamp + timedelta(hours=6)
        )
        self.assertEqual(self.recorder._replays[("LIVEC", "1d")].last_ts, bars[-2].timestamp)

    def test_bar_by_bar_recording_equals_one_batch_replay(self):
        bars = _bars(330)
        self._store_bars("LIVED", "1d", bars[:250])
        self.recorder.record_from_recent_bars(
            ["LIVED"], now=_daily_close(bars[249]) + timedelta(hours=1)
        )
        for lo in range(250, 330, 9):
            chunk = bars[lo : lo + 9]
            self._store_bars("LIVED", "1d", chunk)
            self.recorder.record_from_recent_bars(
                ["LIVED"], now=_daily_close(chunk[-1]) + timedelta(hours=1)
            )
        self.assertEqual(self._scores("LIVED"), replay_trend_scores("LIVED", "1d", bars))
        self.assertEqual(len(self._signals("LIVED", "1d")), 330)

    def test_a_restart_finds_the_rows_and_writes_nothing_new(self):
        bars = _bars(300)
        self._store_bars("LIVEE", "1d", bars)
        now = _daily_close(bars[-1]) + timedelta(hours=1)
        self.recorder.record_from_recent_bars(["LIVEE"], now=now)
        before = self._scores("LIVEE")
        restarted = SignalRecorder()
        self.assertEqual(restarted.record_from_recent_bars(["LIVEE"], now=now), 0)
        self.assertEqual(self._scores("LIVEE"), before)

    def test_only_a_fresh_row_records_the_regime(self):
        bars = _bars(260)
        self._store_bars("LIVEF", "1d", bars)
        self.recorder.record_from_recent_bars(
            ["LIVEF"], now=_daily_close(bars[-1]) + timedelta(minutes=5)
        )
        rows = self._signals("LIVEF", "1d")
        self.assertEqual(rows[-1].market_regime, "risk_on")
        self.assertEqual({r.market_regime for r in rows[:-1]}, {None}, "today's regime on old bars")

        self._store_bars("LIVEG", "1d", bars)
        self.recorder.record_from_recent_bars(
            ["LIVEG"], now=_daily_close(bars[-1]) + timedelta(hours=3)
        )
        self.assertEqual({r.market_regime for r in self._signals("LIVEG", "1d")}, {None})

    def test_the_seeding_budget_seeds_one_pair_a_cycle_cheapest_timeframe_first(self):
        daily, hourly = _bars(260), _hourly_bars(260)
        self._store_bars("LIVEH", "1d", daily)
        self._store_bars("LIVEH", "1h", hourly)
        now = datetime(2026, 1, 1)
        counts = lambda: (len(self._signals("LIVEH", "1d")), len(self._signals("LIVEH", "1h")))  # noqa: E731

        self.assertEqual(
            self.recorder.record_from_recent_bars(["LIVEH"], budget_seconds=0.0, now=now), 260
        )
        self.assertEqual(counts(), (260, 0), "the daily pair is cheaper and goes first")
        self.assertEqual(
            self.recorder.record_from_recent_bars(["LIVEH"], budget_seconds=0.0, now=now), 260
        )
        self.assertEqual(counts(), (260, 260))
        self.assertEqual(
            self.recorder.record_from_recent_bars(["LIVEH"], budget_seconds=0.0, now=now), 0
        )

    def test_one_pairs_failure_does_not_stop_the_others_and_is_retried(self):
        bars = _bars(260)
        self._store_bars("FAILA", "1d", bars)
        self._store_bars("FAILB", "1d", bars)
        now = _daily_close(bars[-1]) + timedelta(hours=1)
        real = SignalRecorder._insert_rows

        def flaky(rec, db, symbol, *a, **k):
            if symbol == "FAILA":
                raise RuntimeError("database is locked")
            return real(rec, db, symbol, *a, **k)

        with (
            patch.object(SignalRecorder, "_insert_rows", flaky),
            self.assertLogs("backend.services.signal_recorder", "ERROR"),
        ):
            self.assertEqual(
                self.recorder.record_from_recent_bars(["FAILA", "FAILB"], now=now), 260
            )
        self.assertEqual(len(self._signals("FAILA", "1d")), 0)
        self.assertEqual(len(self._signals("FAILB", "1d")), 260)

        self.assertEqual(self.recorder.record_from_recent_bars(["FAILA", "FAILB"], now=now), 260)
        self.assertEqual(self._scores("FAILA"), self._scores("FAILB"))

    def test_a_late_arriving_older_bar_is_left_to_the_gap_fill(self):
        bars = _bars(300)
        late = bars[100]
        self._store_bars("LIVEI", "1d", bars[:100] + bars[101:])
        now = _daily_close(bars[-1]) + timedelta(hours=1)
        self.assertEqual(self.recorder.record_from_recent_bars(["LIVEI"], now=now), 299)

        self._store_bars("LIVEI", "1d", [late])
        self.assertEqual(self.recorder.record_from_recent_bars(["LIVEI"], now=now), 0)
        with patch.object(rec_mod, "now_ny", return_value=now):
            self.assertEqual(self.recorder.backfill_signals_for_symbol("LIVEI", timeframe="1d"), 1)

    def test_a_pair_whose_only_bar_is_still_forming_writes_nothing(self):
        bar = _bars(1)[0]
        self._store_bars("LIVEJ", "1d", [bar])
        self.assertEqual(
            self.recorder.record_from_recent_bars(
                ["LIVEJ"], now=bar.timestamp + timedelta(hours=1)
            ),
            0,
        )
        self.assertEqual(self._signals("LIVEJ", "1d"), [])

    def test_no_symbols_is_a_no_op(self):
        self.assertEqual(self.recorder.record_from_recent_bars([]), 0)


class TestHygieneIgnoresAFormingBar(_Db):
    """A forming bar has no signal yet by design; startup must not replay every pair for it."""

    def _minute_bars(self, now):
        last_open = now.replace(second=0, microsecond=0)
        return [
            SimpleNamespace(
                timestamp=last_open - timedelta(minutes=i), close=100.0 + i * 0.01, volume=1000
            )
            for i in range(30, -1, -1)
        ]

    def _sign(self, symbol, bars):
        with self.Session() as db:
            db.add_all(
                [
                    HistoricalSignal(
                        symbol=symbol,
                        timeframe="1m",
                        timestamp=b.timestamp,
                        price=b.close,
                        strategy_version="v1",
                        data_quality="good",
                    )
                    for b in bars
                ]
            )
            db.commit()

    def _run(self, symbol):
        from backend.api import main_helpers

        with (
            patch.object(main_helpers, "SessionLocal", lambda: self.Session()),
            patch.object(
                main_helpers.signal_recorder, "backfill_signals_for_symbol", return_value=0
            ) as bf,
        ):
            main_helpers._fill_signal_gaps(symbol)
        return bf

    def test_the_forming_bar_is_not_a_gap(self):
        from backend.utils.timezone import now_ny

        bars = self._minute_bars(now_ny())
        self._store_bars("HYGA", "1m", bars)
        self._sign("HYGA", bars[:-1])  # every closed bar signed, the forming one not
        self._run("HYGA").assert_not_called()

    def test_a_closed_bar_without_a_signal_still_is(self):
        from backend.utils.timezone import now_ny

        bars = self._minute_bars(now_ny())
        self._store_bars("HYGB", "1m", bars)
        self._sign("HYGB", bars[:-6] + bars[-5:-1])  # one closed bar (bars[-6]) is missing
        self._run("HYGB").assert_called_once()


class TestRelabelSignals(_Db):
    """The one-off repair of rows the old recorder stamped."""

    def test_relabels_from_the_replay_and_leaves_outcomes_alone(self):
        bars = self._stamped()
        with self.Session() as db:
            stats = relabel_signals(db, "RELA", "1d")
        self.assertEqual(stats["rows"], 451)
        self.assertEqual(stats["labelled"], 450 - REPLAY_WARMUP_BARS)
        self.assertEqual(stats["unlabelled"], REPLAY_WARMUP_BARS + 1)  # warm-up + the orphan

        expected = replay_trend_scores("RELA", "1d", bars)
        rows = self._signals("RELA", "1d")
        by_ts = {r.timestamp: r for r in rows}
        for b in bars:
            r = by_ts[b.timestamp]
            self.assertEqual(r.trend_score, expected[b.timestamp])
            self.assertIsNone(r.market_regime)
            self.assertEqual(r.price, b.close)  # untouched
            self.assertIsNotNone(r.return_5b)  # untouched
        self.assertEqual(
            (rows[0].trend_score, rows[0].trend_state, rows[0].market_regime),
            (None, None, None),
            "the orphan has no bar to replay",
        )
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
        self.assertEqual(
            chunked, [(r.timestamp, r.trend_score) for r in self._signals("RELA", "1d")]
        )

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
            original = {
                r.id: (
                    r.trend_score,
                    r.trend_state,
                    r.strength,
                    r.momentum,
                    r.structure,
                    r.market_regime,
                )
                for r in db.query(HistoricalSignal).all()
            }
        fd, path = tempfile.mkstemp(suffix=".csv.gz")
        os.close(fd)
        self.addCleanup(os.unlink, path)

        with self.Session() as db:
            self.assertEqual(export_labels(db, path), 451)
            relabel_signals(db, "RELA", "1d")
            self.assertNotEqual({r.trend_score for r in db.query(HistoricalSignal).all()}, {-5.0})
            self.assertEqual(restore_labels(db, path, chunk_size=50), 451)

        with self.Session() as db:
            restored = {
                r.id: (
                    r.trend_score,
                    r.trend_state,
                    r.strength,
                    r.momentum,
                    r.structure,
                    r.market_regime,
                )
                for r in db.query(HistoricalSignal).all()
            }
        self.assertEqual(restored, original)

    def test_none_labels_survive_the_round_trip(self):
        import os
        import tempfile

        self._stamped()
        fd, path = tempfile.mkstemp(suffix=".csv.gz")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        with self.Session() as db:
            relabel_signals(db, "RELA", "1d")  # warm-up rows become all-None
            export_labels(db, path)
            db.query(HistoricalSignal).update({"trend_state": "bullish", "trend_score": 99.0})
            db.commit()
            restore_labels(db, path)
        rows = self._signals("RELA", "1d")
        self.assertEqual((rows[0].trend_score, rows[0].trend_state), (None, None))


if __name__ == "__main__":
    unittest.main()
