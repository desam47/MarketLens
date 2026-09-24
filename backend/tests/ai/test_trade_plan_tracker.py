"""
Tests for backend.ai.trade_plan_tracker (2026-09-11) — outcome
tracking for explicitly confirmed buy/sell trade-plan calls.

record_trade_plan / get_track_record each open their own SessionLocal()
(patched to an in-memory sqlite sessionmaker below); _grade_row /
_grade_once take a db session directly.
"""

import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.ai import trade_plan_tracker as tracker
from backend.ai.prompt import AnalysisResponse, TradePlan
from backend.models import AITradePlanOutcome
from backend.models.market_data import Bar, DataStatus


def _plan(**over) -> TradePlan:
    base = dict(
        recommendation="buy",
        conviction="medium",
        time_horizon="swing",
        entry_zone_low=100.0,
        entry_zone_high=102.0,
        stop_loss=96.0,
        targets=[108.0, 115.0],
        risk_reward=1.4,
        thesis="Buy the pullback into support with the daily trend up.",
        invalidation="A daily close below 96 breaks the structure.",
    )
    base.update(over)
    return TradePlan(**base)


def _analysis(**over) -> AnalysisResponse:
    base = dict(
        summary="Clean uptrend, buying the dip.",
        trend="bullish",
        confidence=0.7,
        provider="ollama",
        model="llama3.2",
    )
    base.update(over)
    return AnalysisResponse(**base)


def _bar(day: str, *, o, h, low, c) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=datetime.fromisoformat(day),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1_000_000,
        timeframe="1d",
        provider="test",
        data_status=DataStatus.HISTORICAL,
    )


class _DBBase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        AITradePlanOutcome.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()


class TestRecordTradePlan(_DBBase):
    def setUp(self):
        super().setUp()
        p = patch.object(tracker, "SessionLocal", self.Session)
        p.start()
        self.addCleanup(p.stop)
        p2 = patch.object(tracker.settings.ai_trade_plan_tracking, "enabled", True)
        p2.start()
        self.addCleanup(p2.stop)

    def test_buy_plan_captured(self):
        tracker.record_trade_plan("aapl", _analysis(trade_plan=_plan(recommendation="buy")))
        rows = self.db.query(AITradePlanOutcome).all()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.symbol, "AAPL")
        self.assertEqual(row.recommendation, "buy")
        self.assertEqual(row.status, "open")
        self.assertEqual(json.loads(row.targets_json), [108.0, 115.0])
        self.assertEqual(row.provider, "ollama")

    def test_sell_plan_captured(self):
        plan = _plan(
            recommendation="sell",
            entry_zone_low=100.0,
            entry_zone_high=102.0,
            stop_loss=106.0,
            targets=[95.0, 90.0],
        )
        tracker.record_trade_plan("MSFT", _analysis(trade_plan=plan))
        row = self.db.query(AITradePlanOutcome).first()
        self.assertEqual(row.recommendation, "sell")

    def test_hold_plan_skipped(self):
        tracker.record_trade_plan("AAPL", _analysis(trade_plan=_plan(recommendation="hold")))
        self.assertEqual(self.db.query(AITradePlanOutcome).count(), 0)

    def test_no_trade_plan_skipped(self):
        tracker.record_trade_plan("AAPL", _analysis(trade_plan=None))
        self.assertEqual(self.db.query(AITradePlanOutcome).count(), 0)

    def test_disabled_flag_skips_capture(self):
        with patch.object(tracker.settings.ai_trade_plan_tracking, "enabled", False):
            tracker.record_trade_plan("AAPL", _analysis(trade_plan=_plan()))
        self.assertEqual(self.db.query(AITradePlanOutcome).count(), 0)


class TestConfirmedTradePlan(_DBBase):
    def setUp(self):
        super().setUp()
        p = patch.object(tracker, "SessionLocal", self.Session)
        p.start()
        self.addCleanup(p.stop)
        p2 = patch.object(tracker.settings.ai_trade_plan_tracking, "enabled", True)
        p2.start()
        self.addCleanup(p2.stop)

    def test_confirmed_plan_is_deduplicated(self):
        plan = _plan(recommendation="buy")
        first, duplicate_first = tracker.record_confirmed_trade_plan("AAPL", plan)
        second, duplicate_second = tracker.record_confirmed_trade_plan("AAPL", plan)

        self.assertFalse(duplicate_first)
        self.assertTrue(duplicate_second)
        self.assertEqual(first.id, second.id)
        self.assertEqual(self.db.query(AITradePlanOutcome).count(), 1)

    def test_confirmed_hold_plan_is_not_recorded(self):
        row, duplicate = tracker.record_confirmed_trade_plan(
            "AAPL", _plan(recommendation="hold")
        )
        self.assertIsNone(row)
        self.assertFalse(duplicate)
        self.assertEqual(self.db.query(AITradePlanOutcome).count(), 0)


class TestGradeRow(_DBBase):
    def _open_row(self, **over) -> AITradePlanOutcome:
        base = dict(
            symbol="AAPL",
            recommendation="buy",
            conviction="medium",
            time_horizon="swing",
            entry_zone_low=100.0,
            entry_zone_high=102.0,
            stop_loss=96.0,
            targets_json=json.dumps([108.0, 115.0]),
            risk_reward=1.4,
            provider="ollama",
            model="llama3.2",
            created_at=datetime.now() - timedelta(days=1),
            status="open",
        )
        base.update(over)
        row = AITradePlanOutcome(**base)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    @patch("backend.repositories.bar_repository.get_bars")
    def test_buy_target_hit_is_a_win(self, mock_bars):
        row = self._open_row()
        mock_bars.return_value = [
            _bar("2026-01-02", o=101, h=103, low=99, c=102),
            _bar("2026-01-03", o=102, h=109, low=101, c=108),  # crosses first target 108
        ]
        resolved = tracker._grade_row(self.db, row, datetime.now().date())
        self.assertTrue(resolved)
        self.assertEqual(row.status, "win")
        self.assertEqual(row.hit_target_index, 0)
        self.assertEqual(row.resolved_price, 108.0)
        self.assertAlmostEqual(row.return_pct, (108.0 - 101.0) / 101.0, places=4)

    @patch("backend.repositories.bar_repository.get_bars")
    def test_buy_stop_hit_is_a_loss(self, mock_bars):
        row = self._open_row()
        mock_bars.return_value = [_bar("2026-01-02", o=99, h=100, low=95, c=96)]
        resolved = tracker._grade_row(self.db, row, datetime.now().date())
        self.assertTrue(resolved)
        self.assertEqual(row.status, "loss")
        self.assertEqual(row.resolved_price, 96.0)
        self.assertLess(row.return_pct, 0)

    @patch("backend.repositories.bar_repository.get_bars")
    def test_bar_touching_both_stop_and_target_resolves_as_loss(self, mock_bars):
        # conservative rule: can't tell intraday order from OHLC alone
        row = self._open_row()
        mock_bars.return_value = [_bar("2026-01-02", o=100, h=120, low=90, c=110)]
        tracker._grade_row(self.db, row, datetime.now().date())
        self.assertEqual(row.status, "loss")

    @patch("backend.repositories.bar_repository.get_bars")
    def test_sell_target_hit_is_a_win(self, mock_bars):
        row = self._open_row(
            recommendation="sell",
            entry_zone_low=100.0,
            entry_zone_high=102.0,
            stop_loss=106.0,
            targets_json=json.dumps([95.0, 90.0]),
        )
        mock_bars.return_value = [_bar("2026-01-02", o=100, h=101, low=94, c=95)]
        tracker._grade_row(self.db, row, datetime.now().date())
        self.assertEqual(row.status, "win")
        self.assertEqual(row.hit_target_index, 0)
        self.assertGreater(row.return_pct, 0)

    @patch("backend.repositories.bar_repository.get_bars")
    def test_sell_stop_hit_is_a_loss(self, mock_bars):
        # fill-aware: the entry zone must be touched first. bar1 fills
        # [100,102], bar2 (gap-up) hits the stop at 106 → loss.
        row = self._open_row(
            recommendation="sell",
            entry_zone_low=100.0,
            entry_zone_high=102.0,
            stop_loss=106.0,
            targets_json=json.dumps([95.0, 90.0]),
        )
        mock_bars.return_value = [
            _bar("2026-01-02", o=101, h=102, low=100, c=101),  # fills entry zone
            _bar("2026-01-03", o=104, h=107, low=103, c=106),  # gaps up, hits stop
        ]
        tracker._grade_row(self.db, row, datetime.now().date())
        self.assertEqual(row.status, "loss")

    @patch("backend.repositories.bar_repository.get_bars")
    def test_sell_gap_past_entry_without_fill_is_not_graded(self, mock_bars):
        # sell entry [100,102], stop 106; a single bar opening at 104 never
        # enters the entry zone, so the stop hit must NOT grade the row.
        row = self._open_row(
            recommendation="sell",
            entry_zone_low=100.0,
            entry_zone_high=102.0,
            stop_loss=106.0,
            targets_json=json.dumps([95.0, 90.0]),
        )
        mock_bars.return_value = [
            _bar("2026-01-02", o=104, h=107, low=103, c=106),  # gap, no fill
        ]
        resolved = tracker._grade_row(self.db, row, datetime.now().date())
        self.assertFalse(resolved)
        self.assertEqual(row.status, "open")

    @patch("backend.repositories.bar_repository.get_bars")
    def test_buy_gap_past_entry_without_fill_is_not_graded(self, mock_bars):
        # buy entry [100,102], target 108; a single bar opening at 109 never
        # fills the entry zone, so the target touch must NOT grade the row.
        row = self._open_row()
        mock_bars.return_value = [
            _bar("2026-01-02", o=109, h=115, low=109, c=112),  # gap up, no fill
        ]
        resolved = tracker._grade_row(self.db, row, datetime.now().date())
        self.assertFalse(resolved)
        self.assertEqual(row.status, "open")

    @patch("backend.repositories.bar_repository.get_bars")
    def test_unresolved_within_holding_window_stays_open(self, mock_bars):
        row = self._open_row(created_at=datetime.now() - timedelta(days=2))  # swing -> 10 days
        mock_bars.return_value = [_bar("2026-01-02", o=100, h=101, low=99, c=100)]
        resolved = tracker._grade_row(self.db, row, datetime.now().date())
        self.assertFalse(resolved)
        self.assertEqual(row.status, "open")

    @patch("backend.repositories.bar_repository.get_bars")
    def test_expires_after_holding_window_with_mark_to_expiry_return(self, mock_bars):
        row = self._open_row(
            time_horizon="scalp",  # 1-day holding window
            created_at=datetime.now() - timedelta(days=5),
        )
        mock_bars.return_value = [_bar("2026-01-02", o=100, h=101, low=99, c=100.5)]
        resolved = tracker._grade_row(self.db, row, datetime.now().date())
        self.assertTrue(resolved)
        self.assertEqual(row.status, "expired")
        self.assertEqual(row.resolved_price, 100.5)
        self.assertIsNotNone(row.return_pct)

    @patch("backend.repositories.bar_repository.get_bars")
    def test_expires_without_ever_filling_has_no_phantom_return(self, mock_bars):
        # Regression (2026-09-16): a plan whose entry zone gaps past and
        # is never re-touched (buy entry [100,102]; every bar opens and
        # stays above 108, the first target) never became a real
        # position — expiring it must NOT mark it to the last close and
        # compute a return_pct as if one had existed the whole window.
        row = self._open_row(
            time_horizon="scalp",  # 1-day holding window
            created_at=datetime.now() - timedelta(days=5),
        )
        mock_bars.return_value = [
            _bar("2026-01-02", o=112, h=115, low=110, c=113),  # gap, no fill
        ]
        resolved = tracker._grade_row(self.db, row, datetime.now().date())
        self.assertTrue(resolved)
        self.assertEqual(row.status, "expired")
        self.assertIsNone(row.resolved_price)
        self.assertIsNone(row.return_pct)

    @patch("backend.repositories.bar_repository.get_bars")
    def test_no_bars_at_all_stays_open_until_window_expires(self, mock_bars):
        mock_bars.return_value = []
        row = self._open_row(created_at=datetime.now() - timedelta(days=1))
        self.assertFalse(tracker._grade_row(self.db, row, datetime.now().date()))
        row2 = self._open_row(
            time_horizon="scalp",
            created_at=datetime.now() - timedelta(days=5),
        )
        self.assertTrue(tracker._grade_row(self.db, row2, datetime.now().date()))
        self.assertEqual(row2.status, "expired")
        self.assertIsNone(row2.resolved_price)  # no bars to mark against


class TestGradeOnce(_DBBase):
    def _seed(self, **over) -> AITradePlanOutcome:
        base = dict(
            symbol="AAPL",
            recommendation="buy",
            conviction="medium",
            time_horizon="swing",
            entry_zone_low=100.0,
            entry_zone_high=102.0,
            stop_loss=96.0,
            targets_json=json.dumps([108.0]),
            risk_reward=1.4,
            provider="ollama",
            model="llama3.2",
            created_at=datetime.now() - timedelta(days=1),
            status="open",
        )
        base.update(over)
        row = AITradePlanOutcome(**base)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    @patch("backend.repositories.bar_repository.get_bars")
    def test_grades_multiple_open_rows(self, mock_bars):
        self._seed(symbol="AAPL")
        self._seed(symbol="MSFT", targets_json=json.dumps([999.0]))  # far target -> stays open
        mock_bars.return_value = [_bar("2026-01-02", o=101, h=109, low=100, c=108)]

        resolved = tracker._grade_once(self.db)

        self.assertEqual(resolved, 1)
        statuses = {r.symbol: r.status for r in self.db.query(AITradePlanOutcome).all()}
        self.assertEqual(statuses["AAPL"], "win")
        self.assertEqual(statuses["MSFT"], "open")

    def test_a_broken_row_does_not_block_the_batch(self):
        good = self._seed(
            symbol="AAPL", time_horizon="scalp", created_at=datetime.now() - timedelta(days=5)
        )
        bad = self._seed(symbol="MSFT", targets_json="not json")
        with patch("backend.repositories.bar_repository.get_bars", return_value=[]):
            resolved = tracker._grade_once(self.db)
        self.db.refresh(good)
        self.db.refresh(bad)
        self.assertEqual(good.status, "expired")  # resolved despite the sibling's bad row
        self.assertEqual(bad.status, "open")  # left alone, not crashed
        self.assertEqual(resolved, 1)

    def test_no_open_rows_is_a_clean_noop(self):
        self.assertEqual(tracker._grade_once(self.db), 0)


class TestGetTrackRecord(_DBBase):
    def setUp(self):
        super().setUp()
        p = patch.object(tracker, "SessionLocal", self.Session)
        p.start()
        self.addCleanup(p.stop)
        p2 = patch.object(tracker.settings.ai_trade_plan_tracking, "enabled", True)
        p2.start()
        self.addCleanup(p2.stop)

    def _row(self, **over):
        base = dict(
            symbol="AAPL",
            recommendation="buy",
            conviction="medium",
            time_horizon="swing",
            targets_json="[]",
            provider="ollama",
            model="llama3.2",
            created_at=datetime.now(),
            status="win",
            resolved_at=datetime.now(),
            return_pct=0.05,
        )
        base.update(over)
        self.db.add(AITradePlanOutcome(**base))
        self.db.commit()

    def test_disabled_flag_returns_empty(self):
        with patch.object(tracker.settings.ai_trade_plan_tracking, "enabled", False):
            self.assertEqual(tracker.get_track_record("AAPL"), {})

    def test_no_resolved_rows_returns_empty(self):
        self._row(status="open", resolved_at=None, return_pct=None)
        self.assertEqual(tracker.get_track_record("AAPL"), {})

    def test_computes_win_rate_and_avg_returns(self):
        self._row(status="win", return_pct=0.05)
        self._row(status="win", return_pct=0.03)
        self._row(status="loss", return_pct=-0.02)
        self._row(status="open", resolved_at=None, return_pct=None)  # excluded from the sample

        out = tracker.get_track_record("AAPL")

        self.assertEqual(out["sample_size"], 3)
        self.assertAlmostEqual(out["win_rate"], round(2 / 3, 2), places=3)
        self.assertAlmostEqual(out["avg_return_win"], 0.04, places=4)
        self.assertAlmostEqual(out["avg_return_loss"], -0.02, places=4)
        self.assertEqual(out["all_time_open_count"], 1)

    def test_different_symbol_not_mixed_in(self):
        self._row(symbol="AAPL", status="win")
        self._row(symbol="MSFT", status="loss")
        out = tracker.get_track_record("AAPL")
        self.assertEqual(out["sample_size"], 1)
        self.assertEqual(out["win_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
