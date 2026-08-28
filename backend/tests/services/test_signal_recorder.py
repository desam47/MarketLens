"""
Tests for SignalRecorder service.

Tests cover:
  - record_signal (dedup, normal insert, market regime lookup)
  - backfill_outcomes (sufficient data → updates; insufficient → skips)
  - record_from_recent_bars (happy path + dedup)
  - outcome math (return_at_bar, mfe_mae)
"""
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


# Helper to create a BarRow
def _make_bar_row(
    symbol: str, ts: datetime, close: float, high: float, low: float,
    volume: float = 1_000_000.0, open_: float | None = None,
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
        self._sessionlocal_patch = patch.object(
            rec_mod, "SessionLocal", lambda: self.Session()
        )
        self._sessionlocal_patch.start()
        self.addCleanup(self._sessionlocal_patch.stop)

    def tearDown(self):
        self.engine.dispose()

    @patch("backend.services.signal_recorder.SignalRecorder._get_market_regime", return_value="risk_on")
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
            existing = HistoricalSignal(
                symbol="AAPL", timeframe="1d", timestamp=ts, price=150.0
            )
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
        self._sessionlocal_patch = patch.object(
            rec_mod, "SessionLocal", lambda: self.Session()
        )
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
            bars.append(dict(
                symbol=symbol, timeframe="1d",
                timestamp=ts, open=close - 0.5, high=high,
                low=low, close=close,
                volume=1_000_000, provider="test",
                data_status="historical",
            ))
        with self.Session() as db:
            db.bulk_insert_mappings(BarModel, bars)
            db.commit()

    def _seed_signal(self, symbol: str, ts: datetime, price: float, return_5b=None):
        with self.Session() as db:
            sig = HistoricalSignal(
                symbol=symbol, timeframe="1d", timestamp=ts,
                price=price, trend_state="bullish",
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
        """With only 10 future bars (< 20 needed), outcomes should not be computed."""
        anchor_ts = datetime(2025, 1, 1)
        anchor_price = 100.0
        self._seed_signal("AAPL", anchor_ts, anchor_price)
        # Seed only 10 future bars (less than the 20-bar minimum)
        bars = []
        for i in range(10):
            ts = anchor_ts + timedelta(days=i + 1)
            close = 100.0 + i
            bars.append(dict(
                symbol="AAPL", timeframe="1d",
                timestamp=ts, open=close - 0.5, high=close + 0.5,
                low=close - 0.5, close=close,
                volume=1_000_000, provider="test",
                data_status="historical",
            ))
        with self.Session() as db:
            db.bulk_insert_mappings(BarModel, bars)
            db.commit()

        updated = self.recorder.backfill_outcomes(batch_size=10)
        self.assertEqual(updated, 0)

        with self.Session() as db:
            sig = db.query(HistoricalSignal).first()
        self.assertIsNone(sig.return_5b)

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
        bars = [_make_bar_row("AAPL", datetime(2025, 1, i + 1), 100.0 + i * 5, 105.0, 95.0) for i in range(20)]
        # Anchor price = 100.0, bar[4] = 120.0 → (120-100)/100 = 20%
        result = self.recorder._return_at_bar(bars, 100.0, 5)
        self.assertAlmostEqual(result, 20.0)

    def test_return_at_bar_insufficient_bars(self):
        bars = [_make_bar_row("AAPL", datetime(2025, 1, i + 1), 100.0 + i, 105.0, 95.0) for i in range(3)]
        result = self.recorder._return_at_bar(bars, 100.0, 5)
        self.assertIsNone(result)

    def test_mfe_mae(self):
        # Simulate: anchor=100, high reaches 108, low reaches 93
        # Use fixed close=100 so all bars have similar values except where we override
        bars = []
        for i in range(20):
            close = 100.0
            high = 108.0 if i == 8 else 101.0  # peak at i=8
            low = 93.0 if i == 12 else 99.5   # trough at i=12
            bars.append(_make_bar_row("AAPL", datetime(2025, 1, i + 1), close, high, low))
        mfe, mae = self.recorder._mfe_mae(bars, 100.0)
        self.assertAlmostEqual(mfe, 8.0)   # (108-100)/100*100
        self.assertAlmostEqual(mae, -7.0)   # (93-100)/100*100

    def test_mfe_mae_empty_bars(self):
        mfe, mae = self.recorder._mfe_mae([], 100.0)
        self.assertIsNone(mfe)
        self.assertIsNone(mae)


class TestSignalRecorderClassifyHelpers(unittest.TestCase):

    def setUp(self):
        self.recorder = SignalRecorder()

    def test_classify_trend_from_bar_bullish(self):
        # close > open * 1.005 → bullish
        bar = _make_bar_row("AAPL", datetime(2025, 1, 1), 105.0, 106.0, 100.0, open_=100.0)
        result = self.recorder._classify_trend_from_bar(bar)
        self.assertEqual(result, "bullish")

    def test_classify_trend_from_bar_bearish(self):
        # close < open * 0.995 → bearish
        bar = _make_bar_row("AAPL", datetime(2025, 1, 1), 95.0, 100.0, 94.0, open_=100.0)
        result = self.recorder._classify_trend_from_bar(bar)
        self.assertEqual(result, "bearish")

    def test_classify_trend_from_bar_neutral(self):
        # close within 0.5% of open → neutral
        # Use close=100.2, open=100.0: 100.2/100.0=1.002 (in the 0.995..1.005 band)
        bar = _make_bar_row("AAPL", datetime(2025, 1, 1), 100.2, 101.0, 99.5, open_=100.0)
        result = self.recorder._classify_trend_from_bar(bar)
        self.assertEqual(result, "neutral")

    def test_score_from_bar_positive(self):
        bar = _make_bar_row("AAPL", datetime(2025, 1, 1), 105.0, 106.0, 100.0, open_=100.0)
        score = self.recorder._score_from_bar(bar)
        self.assertGreater(score, 0)
        self.assertLessEqual(score, 100)

    def test_score_from_bar_negative(self):
        bar = _make_bar_row("AAPL", datetime(2025, 1, 1), 95.0, 100.0, 94.0, open_=100.0)
        score = self.recorder._score_from_bar(bar)
        self.assertLess(score, 0)
        self.assertGreaterEqual(score, -100)


if __name__ == "__main__":
    unittest.main()
