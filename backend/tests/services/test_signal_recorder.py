"""
Tests for SignalRecorder service.

Tests cover:
  - record_signal (dedup, normal insert, market regime lookup)
  - backfill_outcomes (sufficient data → updates; insufficient → skips)
  - record_from_recent_bars (happy path + dedup)
  - outcome math (return_at_bar, mfe_mae)
"""

import json
import os
import sys
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from backend.models.market_data_sql import BarModel
from backend.models.signal import HistoricalSignal
from backend.services.signal_recorder import SignalRecorder


# Plain dataclass used instead of MagicMock — avoids spec attribute filtering issues.
@dataclass
class BarRow:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 1_000_000.0
    timeframe: str = "1m"


# Helper to create a BarRow
def _make_bar_row(
    symbol: str,
    ts: datetime,
    close: float,
    high: float,
    low: float,
    volume: float = 1_000_000.0,
    open_: float | None = None,
):
    return BarRow(
        symbol=symbol,
        timestamp=ts,
        open=open_ if open_ is not None else close,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


class TestSignalRecorderRecordSignal(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        HistoricalSignal.__table__.create(self.engine, checkfirst=True)
        BarModel.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        # Per-test recorder with fresh state
        self.recorder = SignalRecorder()
        # Patch SessionLocal in the recorder module to use our test engine
        from backend.services import signal_recorder as rec_mod

        self._sessionlocal_patch = patch.object(rec_mod, "SessionLocal", lambda: self.Session())
        self._sessionlocal_patch.start()
        self.addCleanup(self._sessionlocal_patch.stop)

    def tearDown(self):
        self.engine.dispose()

    @patch(
        "backend.services.signal_recorder.SignalRecorder._get_market_regime", return_value="risk_on"
    )
    def test_record_signal_inserts_row(self, _mock_regime):
        sig = self.recorder.record_signal(
            symbol="AAPL",
            timeframe="1d",
            trend_score=25.0,
            trend_state="bullish",
            price=150.0,
            timestamp=datetime(2025, 1, 1),
        )
        self.assertIsNotNone(sig)
        self.assertEqual(sig.symbol, "AAPL")
        self.assertEqual(sig.trend_state, "bullish")
        self.assertEqual(sig.price, 150.0)

    def test_record_signal_normalizes_symbol_uppercase(self):
        sig = self.recorder.record_signal(
            symbol="aapl",
            timeframe="1d",
            timestamp=datetime(2025, 1, 1),
        )
        self.assertEqual(sig.symbol, "AAPL")

    def test_record_signal_dedup_returns_none(self):
        ts = datetime(2025, 1, 1)
        self.recorder.record_signal(symbol="AAPL", timeframe="1d", timestamp=ts)
        second = self.recorder.record_signal(symbol="AAPL", timeframe="1d", timestamp=ts)
        self.assertIsNone(second)

    def test_record_signal_skips_existing_db_row(self):
        ts = datetime(2025, 1, 1)
        # Insert via repo first
        with self.Session() as db:
            existing = HistoricalSignal(symbol="AAPL", timeframe="1d", timestamp=ts, price=150.0)
            db.add(existing)
            db.commit()
        # Recorder should skip
        result = self.recorder.record_signal(symbol="AAPL", timeframe="1d", timestamp=ts)
        self.assertIsNone(result)


class TestSignalRecorderBackfillOutcomes(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        HistoricalSignal.__table__.create(self.engine, checkfirst=True)
        BarModel.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.recorder = SignalRecorder()
        from backend.services import signal_recorder as rec_mod

        self._sessionlocal_patch = patch.object(rec_mod, "SessionLocal", lambda: self.Session())
        self._sessionlocal_patch.start()
        self.addCleanup(self._sessionlocal_patch.stop)

    def tearDown(self):
        self.engine.dispose()

    def _seed_bars(self, symbol: str, anchor_ts: datetime, close_start: float):
        """Seed 25 future bars starting from anchor_ts+1 day."""
        bars = []
        for i in range(25):
            ts = anchor_ts + timedelta(days=i + 1)
            close = close_start * (1 + 0.01 * i)
            high = close * 1.005
            low = close * 0.995
            bars.append(
                dict(
                    symbol=symbol,
                    timeframe="1d",
                    timestamp=ts,
                    open=close - 0.5,
                    high=high,
                    low=low,
                    close=close,
                    volume=1_000_000,
                    provider="test",
                    data_status="historical",
                )
            )
        with self.Session() as db:
            db.bulk_insert_mappings(BarModel, bars)
            db.commit()

    def _seed_signal(self, symbol: str, ts: datetime, price: float, return_5b=None):
        with self.Session() as db:
            sig = HistoricalSignal(
                symbol=symbol,
                timeframe="1d",
                timestamp=ts,
                price=price,
                trend_state="bullish",
                _outcome_missing=True,
            )
            db.add(sig)
            db.commit()
            db.refresh(sig)
            return sig.id

    def test_backfill_outcomes_computes_all_outcomes(self):
        """With 25 future bars, all outcomes (5/10/20/MFE/MAE) should be computed."""
        anchor_ts = datetime(2025, 1, 1)
        anchor_price = 100.0
        self._seed_signal("AAPL", anchor_ts, anchor_price)
        self._seed_bars("AAPL", anchor_ts, anchor_price)

        updated = self.recorder.backfill_outcomes(batch_size=10)
        self.assertEqual(updated, 1)

        with self.Session() as db:
            sig = db.query(HistoricalSignal).first()
        self.assertIsNotNone(sig.return_5b)
        self.assertIsNotNone(sig.return_10b)
        self.assertIsNotNone(sig.return_20b)
        self.assertIsNotNone(sig.mfe)
        self.assertIsNotNone(sig.mae)
        self.assertEqual(sig._outcome_missing, False)

    def test_backfill_outcomes_skips_when_insufficient_bars(self):
        """With zero future bars, the row is left alone (no MFE/MAE possible)."""
        anchor_ts = datetime(2025, 1, 1)
        anchor_price = 100.0
        self._seed_signal("AAPL", anchor_ts, anchor_price)
        # No future bars seeded at all.

        updated = self.recorder.backfill_outcomes(batch_size=10)
        self.assertEqual(updated, 0)

        with self.Session() as db:
            sig = db.query(HistoricalSignal).first()
        self.assertIsNone(sig.return_5b)

    def test_backfill_outcomes_partial_row_matures_on_later_pass(self):
        """A partial row stays queued until its complete 20-bar outcome exists."""
        anchor_ts = datetime(2025, 1, 1)
        anchor_price = 100.0
        self._seed_signal("AAPL", anchor_ts, anchor_price)
        # Seed only 10 future bars.
        bars = []
        for i in range(10):
            ts = anchor_ts + timedelta(days=i + 1)
            close = 100.0 + i
            bars.append(
                dict(
                    symbol="AAPL",
                    timeframe="1d",
                    timestamp=ts,
                    open=close - 0.5,
                    high=close + 0.5,
                    low=close - 0.5,
                    close=close,
                    volume=1_000_000,
                    provider="test",
                    data_status="historical",
                )
            )
        with self.Session() as db:
            db.bulk_insert_mappings(BarModel, bars)
            db.commit()

        updated = self.recorder.backfill_outcomes(batch_size=10)
        # Signal IS updated (we wrote what we had); 20b is still None.
        self.assertEqual(updated, 1)

        with self.Session() as db:
            sig = db.query(HistoricalSignal).first()
        self.assertIsNotNone(sig.return_5b)
        self.assertIsNotNone(sig.return_10b)
        self.assertIsNone(sig.return_20b)  # not enough bars
        self.assertIsNone(sig.mfe)  # excursions use the completed 20-bar window
        self.assertIsNone(sig.mae)
        self.assertTrue(sig._outcome_missing)

        # Add bars 11 through 20 and prove a later pass revisits this same row.
        with self.Session() as db:
            for i in range(10, 20):
                close = 100.0 + i
                db.add(BarModel(
                    symbol="AAPL", timeframe="1d", timestamp=anchor_ts + timedelta(days=i + 1),
                    open=close - 0.5, high=close + 0.5, low=close - 0.5, close=close,
                    volume=1_000_000, provider="test", data_status="historical",
                ))
            db.commit()

        self.assertEqual(self.recorder.backfill_outcomes(batch_size=10), 1)
        with self.Session() as db:
            sig = db.query(HistoricalSignal).first()
        self.assertIsNotNone(sig.return_20b)
        self.assertIsNotNone(sig.mfe)
        self.assertIsNotNone(sig.mae)
        self.assertFalse(sig._outcome_missing)

    def test_backfill_outcomes_is_not_blocked_by_rows_that_cannot_finish(self):
        """HS-15: a full batch of stuck older rows must not starve a newer finishable row."""
        # NOK stopped updating: two bars after its signals, never five.
        for day in (1, 2, 3):
            self._seed_signal("NOK", datetime(2025, 1, day), 10.0)
        with self.Session() as db:
            for day in (4, 5):
                db.add(BarModel(
                    symbol="NOK", timeframe="1d", timestamp=datetime(2025, 1, day),
                    open=10.0, high=10.1, low=9.9, close=10.0, volume=1_000,
                    provider="test", data_status="historical",
                ))
            db.commit()
        newer = datetime(2025, 3, 1)
        self._seed_signal("AAPL", newer, 100.0)
        self._seed_bars("AAPL", newer, 100.0)

        self.assertEqual(self.recorder.backfill_outcomes(batch_size=2), 1)
        with self.Session() as db:
            aapl = db.query(HistoricalSignal).filter_by(symbol="AAPL").one()
            nok = db.query(HistoricalSignal).filter_by(symbol="NOK").all()
        self.assertIsNotNone(aapl.return_20b)
        self.assertFalse(aapl._outcome_missing)
        self.assertTrue(all(sig.return_5b is None for sig in nok))

    def test_backfill_outcomes_zero_candidates(self):
        """No signals needing outcomes → returns 0."""
        updated = self.recorder.backfill_outcomes(batch_size=10)
        self.assertEqual(updated, 0)

    def test_backfill_outcomes_batches_correctly(self):
        """Two signals: first has enough bars, second doesn't → only 1 updated."""
        anchor_ts = datetime(2025, 1, 1)
        price = 100.0
        self._seed_signal("AAPL", anchor_ts, price)
        self._seed_bars("AAPL", anchor_ts, price)
        # Second signal has no bars at all
        self._seed_signal("MSFT", anchor_ts, price)

        updated = self.recorder.backfill_outcomes(batch_size=50)
        self.assertEqual(updated, 1)

    def test_backfill_outcomes_bulk_prefetch_multiple_symbols(self):
        """Regression: bulk pre-fetch must not cross-contaminate (symbol, timeframe).

        Two symbols (AAPL, MSFT) each with a different number of future bars.
        The bulk pre-fetch query must not accidentally reuse AAPL's bars for MSFT.
        """
        # AAPL: signal at Jan 1 + 5 future bars → partial fill (5b filled, 10b None)
        aapl_anchor = datetime(2025, 1, 1)
        self._seed_signal("AAPL", aapl_anchor, 100.0)
        for i in range(1, 6):
            ts = aapl_anchor + timedelta(days=i)
            close = 100.0 + i
            with self.Session() as db:
                db.add(
                    BarModel(
                        symbol="AAPL",
                        timeframe="1d",
                        timestamp=ts,
                        open=close - 0.1,
                        high=close + 0.1,
                        low=close - 0.1,
                        close=close,
                        volume=1_000_000,
                        provider="test",
                        data_status="historical",
                    )
                )
                db.commit()

        # MSFT: signal at Jan 1 + 15 future bars → partial fill (5b+10b filled, 20b None)
        msft_anchor = datetime(2025, 1, 1)
        self._seed_signal("MSFT", msft_anchor, 100.0)
        for i in range(1, 16):
            ts = msft_anchor + timedelta(days=i)
            close = 100.0 + i
            with self.Session() as db:
                db.add(
                    BarModel(
                        symbol="MSFT",
                        timeframe="1d",
                        timestamp=ts,
                        open=close - 0.1,
                        high=close + 0.1,
                        low=close - 0.1,
                        close=close,
                        volume=1_000_000,
                        provider="test",
                        data_status="historical",
                    )
                )
                db.commit()

        updated = self.recorder.backfill_outcomes(batch_size=50)
        self.assertEqual(updated, 2)  # both updated

        # Verify AAPL: only 5b filled (5 bars available)
        with self.Session() as db:
            aapl_sig = db.query(HistoricalSignal).filter_by(symbol="AAPL").first()
            msft_sig = db.query(HistoricalSignal).filter_by(symbol="MSFT").first()

        self.assertIsNotNone(aapl_sig)
        self.assertIsNotNone(aapl_sig.return_5b)  # 5 bars → 5b filled
        self.assertIsNone(aapl_sig.return_10b)  # <10 bars → None
        self.assertIsNone(aapl_sig.return_20b)  # <20 bars → None
        self.assertIsNone(aapl_sig.mfe)  # excursion waits for the 20-bar horizon
        self.assertIsNone(aapl_sig.mae)

        self.assertIsNotNone(msft_sig)
        self.assertIsNotNone(msft_sig.return_5b)  # 15 bars → 5b filled
        self.assertIsNotNone(msft_sig.return_10b)  # 15 bars → 10b filled
        self.assertIsNone(msft_sig.return_20b)  # <20 bars → None
        self.assertIsNone(msft_sig.mfe)
        self.assertIsNone(msft_sig.mae)

    def test_backfill_outcomes_bulk_prefetch_two_signals_same_symbol(self):
        """Regression: two signals for the same symbol are handled independently.

        Signal A at Jan 1 (5 future bars → partial).
        Signal B at Jan 7 (1 future bar → still pending, not selected).
        Both are in the same (AAPL, 1d) bucket but must not share results.
        """
        anchor = datetime(2025, 1, 1)
        price = 100.0

        # Signal A at Jan 1
        self._seed_signal("AAPL", anchor, price)
        # 5 future bars
        for i in range(1, 6):
            ts = anchor + timedelta(days=i)
            close = price + i
            with self.Session() as db:
                db.add(
                    BarModel(
                        symbol="AAPL",
                        timeframe="1d",
                        timestamp=ts,
                        open=close - 0.1,
                        high=close + 0.1,
                        low=close - 0.1,
                        close=close,
                        volume=1_000_000,
                        provider="test",
                        data_status="historical",
                    )
                )
                db.commit()

        # Signal B at Jan 7 (1 future bar)
        sig_b_ts = datetime(2025, 1, 7)
        self._seed_signal("AAPL", sig_b_ts, price)
        with self.Session() as db:
            db.add(
                BarModel(
                    symbol="AAPL",
                    timeframe="1d",
                    timestamp=datetime(2025, 1, 8),
                    open=110.1,
                    high=110.2,
                    low=110.0,
                    close=110.1,
                    volume=1_000_000,
                    provider="test",
                    data_status="historical",
                )
            )
            db.commit()

        # Only A is selected: B's single later bar cannot fill any window yet (HS-15).
        updated = self.recorder.backfill_outcomes(batch_size=50)
        self.assertEqual(updated, 1)

        with self.Session() as db:
            sigs = (
                db.query(HistoricalSignal)
                .filter_by(symbol="AAPL")
                .order_by(HistoricalSignal.timestamp.asc())
                .all()
            )

        self.assertEqual(len(sigs), 2)

        # Signal A (Jan 1): 5 bars available
        self.assertEqual(sigs[0].timestamp, anchor)
        self.assertIsNotNone(sigs[0].return_5b)
        self.assertIsNone(sigs[0].return_10b)  # only 5 bars
        self.assertIsNone(sigs[0].return_20b)
        self.assertIsNone(sigs[0].mfe)
        self.assertIsNone(sigs[0].mae)

        # Signal B (Jan 7): one bar is not enough for any complete outcome.
        self.assertEqual(sigs[1].timestamp, sig_b_ts)
        self.assertIsNone(sigs[1].return_5b)  # 1 bar not enough for 5b
        self.assertIsNone(sigs[1].return_10b)
        self.assertIsNone(sigs[1].return_20b)
        self.assertIsNone(sigs[1].mfe)
        self.assertIsNone(sigs[1].mae)


class TestSignalRecorderBackfillSignals(unittest.TestCase):
    """Tests for backfill_signals_for_symbol (the bulk signal-recording path)."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        HistoricalSignal.__table__.create(self.engine, checkfirst=True)
        BarModel.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.recorder = SignalRecorder()
        from backend.services import signal_recorder as rec_mod

        self._sessionlocal_patch = patch.object(rec_mod, "SessionLocal", lambda: self.Session())
        self._sessionlocal_patch.start()
        self.addCleanup(self._sessionlocal_patch.stop)

    def tearDown(self):
        self.engine.dispose()

    def test_backfill_signals_for_symbol_bulk_inserts_all(self):
        """Bulk path creates one signal per bar, correctly populating all fields."""
        sym = "BULK1"
        tf = "1m"
        base = datetime(2025, 3, 1, 9, 30)
        bars = [
            BarModel(
                symbol=sym,
                timeframe=tf,
                timestamp=base + timedelta(minutes=i),
                open=100.0 + i * 0.1,
                high=100.5 + i * 0.1,
                low=99.5 + i * 0.1,
                close=100.2 + i * 0.1,
                volume=10_000,
                provider="test",
                data_status="historical",
            )
            for i in range(300)
        ]
        with self.Session() as db:
            db.bulk_save_objects(bars)
            db.commit()

        recorded = self.recorder.backfill_signals_for_symbol(sym, timeframe=tf)
        self.assertEqual(recorded, 300)

        with self.Session() as db:
            signals = (
                db.query(HistoricalSignal)
                .filter_by(symbol=sym)
                .order_by(HistoricalSignal.timestamp.asc())
                .all()
            )
        self.assertEqual(len(signals), 300)
        # Spot-check fields
        self.assertEqual(signals[0].symbol, sym)
        self.assertEqual(signals[0].timeframe, tf)
        self.assertIsNotNone(signals[0].price)
        # The first bars are the trend engine's warm-up: a row exists, its label is "unknown".
        # Once warmed up, every bar is labelled from a replay of the bars before it.
        self.assertIsNone(signals[0].trend_score)
        self.assertIsNone(signals[0].trend_state)
        self.assertIsNotNone(signals[-1].trend_state)
        self.assertIsNotNone(signals[-1].trend_score)
        # HS-09: no placeholder evidence. Nothing assesses volume or data quality here, so both
        # stay empty; the version comes from settings and the bar's provenance is kept.
        from backend.config.settings import settings

        self.assertIsNone(signals[-1].volume_state)
        self.assertIsNone(signals[-1].data_quality)
        self.assertEqual(signals[-1].strategy_version, settings.trend.strategy_version)
        inputs = json.loads(signals[-1].confidence_inputs)
        self.assertEqual((inputs["bar_provider"], inputs["bar_data_status"]), ("test", "historical"))

    def test_insert_rows_race_with_another_writer_is_a_no_op(self):
        """HS-12: a batch that lost the race to another writer skips the bar, not fails."""
        from types import SimpleNamespace

        bar = SimpleNamespace(
            timestamp=datetime(2025, 3, 3), close=101.0, high=102.0, low=100.0,
            provider="test", data_status="historical",
        )
        now = datetime(2026, 1, 1)
        with self.Session() as db:
            self.assertEqual(self.recorder._insert_rows(db, "RACE", "1d", [(bar, 10.0)], now), 1)
            # A second writer whose "already recorded?" check ran before the first commit.
            self.assertEqual(self.recorder._insert_rows(db, "RACE", "1d", [(bar, 10.0)], now), 0)
        with self.Session() as db:
            self.assertEqual(db.query(HistoricalSignal).filter_by(symbol="RACE").count(), 1)

    def test_backfill_signals_for_symbol_dedup_skips_existing(self):
        """Second call records 0 new signals (DB dedup + in-process cache)."""
        sym = "BULK2"
        tf = "1d"
        base = datetime(2025, 1, 1)
        bars = [
            BarModel(
                symbol=sym,
                timeframe=tf,
                timestamp=base + timedelta(days=i),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1_000_000,
                provider="test",
                data_status="historical",
            )
            for i in range(50)
        ]
        with self.Session() as db:
            db.bulk_save_objects(bars)
            db.commit()

        first = self.recorder.backfill_signals_for_symbol(sym, timeframe=tf)
        self.assertEqual(first, 50)

        second = self.recorder.backfill_signals_for_symbol(sym, timeframe=tf)
        self.assertEqual(second, 0)  # all already exist

        with self.Session() as db:
            count = db.query(HistoricalSignal).filter_by(symbol=sym).count()
        self.assertEqual(count, 50)

    def test_backfill_signals_for_symbol_respects_max_bars(self):
        """Capping at max_bars limits the rows processed."""
        sym = "BULK3"
        tf = "1m"
        base = datetime(2025, 3, 1, 9, 30)
        bars = [
            BarModel(
                symbol=sym,
                timeframe=tf,
                timestamp=base + timedelta(minutes=i),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=10_000,
                provider="test",
                data_status="historical",
            )
            for i in range(200)
        ]
        with self.Session() as db:
            db.bulk_save_objects(bars)
            db.commit()

        recorded = self.recorder.backfill_signals_for_symbol(sym, timeframe=tf, max_bars=30)
        self.assertEqual(recorded, 30)

        with self.Session() as db:
            count = db.query(HistoricalSignal).filter_by(symbol=sym).count()
        self.assertEqual(count, 30)


class TestSignalRecorderHelpers(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        HistoricalSignal.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.recorder = SignalRecorder()

    def tearDown(self):
        self.engine.dispose()

    def test_return_at_bar_exact(self):
        bars = [
            _make_bar_row("AAPL", datetime(2025, 1, i + 1), 100.0 + i * 5, 105.0, 95.0)
            for i in range(20)
        ]
        # Anchor price = 100.0, bar[4] = 120.0 → (120-100)/100 = 20%
        result = self.recorder._return_at_bar(bars, 100.0, 5)
        self.assertAlmostEqual(result, 20.0)

    def test_return_at_bar_insufficient_bars(self):
        bars = [
            _make_bar_row("AAPL", datetime(2025, 1, i + 1), 100.0 + i, 105.0, 95.0)
            for i in range(3)
        ]
        result = self.recorder._return_at_bar(bars, 100.0, 5)
        self.assertIsNone(result)

    def test_mfe_mae(self):
        # Simulate: anchor=100, high reaches 108, low reaches 93
        # Use fixed close=100 so all bars have similar values except where we override
        bars = []
        for i in range(20):
            close = 100.0
            high = 108.0 if i == 8 else 101.0  # peak at i=8
            low = 93.0 if i == 12 else 99.5  # trough at i=12
            bars.append(_make_bar_row("AAPL", datetime(2025, 1, i + 1), close, high, low))
        mfe, mae = self.recorder._mfe_mae(bars, 100.0)
        self.assertAlmostEqual(mfe, 8.0)  # (108-100)/100*100
        self.assertAlmostEqual(mae, -7.0)  # (93-100)/100*100

    def test_mfe_mae_empty_bars(self):
        mfe, mae = self.recorder._mfe_mae([], 100.0)
        self.assertIsNone(mfe)
        self.assertIsNone(mae)


if __name__ == "__main__":
    unittest.main()
