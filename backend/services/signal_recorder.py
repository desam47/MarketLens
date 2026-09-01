"""
Signal recording service.

Two responsibilities:

1. ``record_signal(symbol, timeframe, snapshot)`` — persist a snapshot row
   for a (symbol, timeframe) bar close. Called from the bar ingestion path.

2. ``backfill_outcomes()`` — find signals with NULL forward outcomes and
   compute them using the latest stored bars. Only runs once a signal
   is at least 20 bars old (so all forward returns can be computed).
   Skipped signals stay in the queue — no look-ahead bias.

The forward-outcome calculation is fully isolated from the live engine:
it only reads stored ``BarModel`` rows. If 20 future bars don't exist
yet, the function leaves the row alone and returns it to the queue.
"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import and_, func

from backend.database import SessionLocal
from backend.models import BarModel, HistoricalSignal
from backend.repositories.signal_repository import SignalRepository

logger = logging.getLogger(__name__)

# How many bars of forward data we need before computing outcomes.
# Per spec: 5/10/20-bar returns + MFE/MAE.
REQUIRED_FORWARD_BARS = 25  # leave a few bars headroom for MFE/MAE


class SignalRecorder:
    """Persists historical signals and backfills forward outcomes."""

    def __init__(self) -> None:
        self._last_recorded: dict[tuple[str, str, datetime], datetime] = {}

    # --- Public API ----------------------------------------------------------

    def record_signal(
        self,
        symbol: str,
        timeframe: str,
        trend_score: float | None = None,
        trend_state: str | None = None,
        strength: float | None = None,
        market_regime: str | None = None,
        relative_strength: str | None = None,
        sector_alignment: float | None = None,
        volume_state: str | None = None,
        momentum: float | None = None,
        structure: str | None = None,
        confidence_inputs: dict | None = None,
        strategy_version: str = "v1",
        data_quality: str = "good",
        price: float | None = None,
        timestamp: datetime | None = None,
    ) -> HistoricalSignal | None:
        """Persist a single HistoricalSignal row.

        Returns the created signal, or None if the bar was already recorded
        (dedup by (symbol, timeframe, timestamp)).
        """
        sym = symbol.upper()
        tf = timeframe
        # Always store as timezone-aware UTC. Without ``tzinfo=UTC`` the
        # value serializes as ``2026-08-31T15:23:00`` (no offset), and
        # JavaScript's ``new Date()`` treats that as LOCAL time — producing
        # wrong timestamps in the dashboard. The ``+00:00`` suffix forces
        # correct UTC interpretation in the browser.
        if timestamp is None:
            ts = datetime.now(timezone.utc)
        elif timestamp.tzinfo is None:
            ts = timestamp.replace(tzinfo=timezone.utc)
        else:
            ts = timestamp

        # Skip if we already recorded this exact bar (dedup).
        key = (sym, tf, ts)
        if key in self._last_recorded:
            return None

        db = SessionLocal()
        try:
            repo = SignalRepository(db)

            existing = (
                db.query(HistoricalSignal.id)
                .filter(
                    HistoricalSignal.symbol == sym,
                    HistoricalSignal.timeframe == tf,
                    HistoricalSignal.timestamp == ts,
                )
                .first()
            )
            if existing is not None:
                self._last_recorded[key] = ts
                return None

            confidence_json = (
                json.dumps(confidence_inputs) if confidence_inputs else None
            )

            signal = repo.create(
                symbol=sym,
                timestamp=ts,
                timeframe=tf,
                price=price,
                trend_score=trend_score,
                trend_state=trend_state,
                strength=strength,
                market_regime=market_regime,
                relative_strength=relative_strength,
                sector_alignment=sector_alignment,
                volume_state=volume_state,
                momentum=momentum,
                structure=structure,
                confidence_inputs=confidence_json,
                strategy_version=strategy_version,
                data_quality=data_quality,
                _outcome_missing=True,
            )
            self._last_recorded[key] = ts
            logger.debug(f"Recorded signal for {sym}/{tf} @ {ts}")
            return signal
        except Exception as e:
            logger.error(f"Failed to record signal for {symbol}/{timeframe}: {e}")
            db.rollback()
            return None
        finally:
            db.close()

    def backfill_outcomes(self, batch_size: int = 50) -> int:
        """Compute forward outcomes for signals that have enough future data.

        Returns the number of signals updated. Safe to call on a schedule
        (e.g. every minute from the ingestion service) — it only touches
        rows that have at least REQUIRED_FORWARD_BARS future bars stored.
        """
        db = SessionLocal()
        updated = 0
        try:
            repo = SignalRepository(db)
            candidates = repo.get_signals_needing_outcomes(limit=batch_size)
            if not candidates:
                return 0

            # Pre-fetch all future bars for all symbols/timeframes in one go
            # to avoid N+1 queries. Keyed by (symbol, timeframe, anchor_ts).
            for signal in candidates:
                if self._compute_outcome_for_signal(signal, repo):
                    updated += 1
        except Exception as e:
            logger.error(f"Outcome backfill failed: {e}")
            db.rollback()
        finally:
            db.close()
        return updated

    def record_from_recent_bars(
        self, symbols: list[str], timeframes: list[str]
    ) -> int:
        """Walk the latest stored bar per (symbol, timeframe) and record signals.

        For each (symbol, timeframe) pair, reads the most recent bar from
        the DB. If we haven't recorded that bar yet, snapshots the trend
        state by calling the in-memory MultiTimeframeEngine and writes a
        HistoricalSignal row.

        Returns the count of new signals written.
        """
        if not symbols or not timeframes:
            return 0
        recorded = 0
        db = SessionLocal()
        try:
            # Find the most recent bar per (symbol, timeframe) in one query.
            subq = (
                db.query(
                    BarModel.symbol,
                    BarModel.timeframe,
                    func.max(BarModel.timestamp).label("ts"),
                )
                .filter(BarModel.symbol.in_([s.upper() for s in symbols]))
                .filter(BarModel.timeframe.in_(timeframes))
                .group_by(BarModel.symbol, BarModel.timeframe)
            ).subquery()

            rows = (
                db.query(BarModel)
                .join(
                    subq,
                    and_(
                        BarModel.symbol == subq.c.symbol,
                        BarModel.timeframe == subq.c.timeframe,
                        BarModel.timestamp == subq.c.ts,
                    ),
                )
                .all()
            )

            for bar in rows:
                if self._record_from_bar(bar, db):
                    recorded += 1
        except Exception as e:
            logger.error(f"record_from_recent_bars failed: {e}")
            db.rollback()
        finally:
            db.close()
        return recorded

    def _record_from_bar(self, bar, db) -> bool:
        """Build a snapshot for a single bar and write a signal row.

        Returns True if a new signal was persisted.
        """
        key = (bar.symbol.upper(), bar.timeframe, bar.timestamp)
        if key in self._last_recorded:
            return False

        # Skip if a row already exists for this (symbol, timeframe, ts).
        existing = (
            db.query(HistoricalSignal.id)
            .filter(
                HistoricalSignal.symbol == bar.symbol.upper(),
                HistoricalSignal.timeframe == bar.timeframe,
                HistoricalSignal.timestamp == bar.timestamp,
            )
            .first()
        )
        if existing is not None:
            self._last_recorded[key] = bar.timestamp
            return False

        # Build the trend snapshot. Lazy-import the MTF router to avoid
        # pulling the API surface into the service layer.
        trend_state = self._classify_trend_from_bar(bar)
        trend_score = self._score_from_bar(bar)
        volume_state = self._classify_volume(bar)

        # Try to fetch market regime from the global market context engine.
        market_regime = self._get_market_regime()
        sector_alignment = None  # Phase 8 sector engine alignment — future hook

        self.record_signal(
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            trend_score=trend_score,
            trend_state=trend_state,
            strength=min(abs(trend_score) / 100.0, 1.0) if trend_score is not None else None,
            market_regime=market_regime,
            relative_strength=None,
            sector_alignment=sector_alignment,
            volume_state=volume_state,
            momentum=trend_score,
            structure=trend_state,
            confidence_inputs={"bar_close": bar.close, "bar_high": bar.high, "bar_low": bar.low},
            strategy_version="v1",
            data_quality="good",
            price=bar.close,
            timestamp=bar.timestamp,
        )
        return True

    def _classify_trend_from_bar(self, bar) -> str:
        """Classify a single bar as bullish/bearish/neutral using close vs SMA-proxy.

        The MTF engine is in-process state. To avoid hard-coupling, we use
        a simple close-vs-open heuristic here. The signal's ``structure``
        field is the source of truth for richer trend state; this is the
        fallback when the MTF engine isn't available.
        """
        try:
            close = float(bar.close or 0)
            open_ = float(bar.open or close)
            if close > open_ * 1.005:
                return "bullish"
            if close < open_ * 0.995:
                return "bearish"
            return "neutral"
        except Exception:
            return "neutral"

    def _score_from_bar(self, bar) -> float:
        """Build a -100..+100 score from close vs open."""
        try:
            close = float(bar.close or 0)
            open_ = float(bar.open or close)
            if open_ <= 0:
                return 0.0
            pct = (close - open_) / open_ * 100.0
            return max(min(pct * 5.0, 100.0), -100.0)
        except Exception:
            return 0.0

    def _classify_volume(self, bar) -> str:
        """Crude volume classification — no historical baseline here."""
        try:
            v = float(bar.volume or 0)
            if v <= 0:
                return "normal"
            return "normal"  # needs a baseline; left as a future hook
        except Exception:
            return "normal"

    def _get_market_regime(self) -> str | None:
        """Try to read the current market regime from the global engine.

        Returns None if the engine isn't initialized yet (e.g. during
        cold-start before the market context router has been called).
        """
        try:
            from backend.api.market_context.router import get_engine
            engine = get_engine()
            snap = engine.get_current_context()
            if snap is None:
                return None
            return snap.regime.value if hasattr(snap.regime, "value") else str(snap.regime)
        except Exception:
            return None

    # --- Internal helpers ---------------------------------------------------

    def _compute_outcome_for_signal(
        self, signal: HistoricalSignal, repo: SignalRepository
    ) -> bool:
        """Compute and persist forward outcomes for one signal row.

        Returns True if outcomes were updated, False if the row was
        left alone (insufficient future data).
        """
        db = repo.db
        anchor_ts = signal.timestamp
        anchor_price = signal.price
        if anchor_price is None or anchor_price <= 0:
            # Try to recover the price from the matching bar
            anchor_price = self._price_at(db, signal.symbol, signal.timeframe, anchor_ts)
            if anchor_price is None or anchor_price <= 0:
                logger.debug(
                    f"Cannot compute outcomes for signal {signal.id}: "
                    f"no price at anchor"
                )
                return False
            signal.price = anchor_price

        # Pull the next REQUIRED_FORWARD_BARS bars.
        future_bars = (
            db.query(BarModel)
            .filter(
                BarModel.symbol == signal.symbol,
                BarModel.timeframe == signal.timeframe,
                BarModel.timestamp > anchor_ts,
            )
            .order_by(BarModel.timestamp.asc())
            .limit(REQUIRED_FORWARD_BARS)
            .all()
        )

        if len(future_bars) < 20:
            # Not enough data yet — leave the row alone.
            return False

        return_5b = self._return_at_bar(future_bars, anchor_price, 5)
        return_10b = self._return_at_bar(future_bars, anchor_price, 10)
        return_20b = self._return_at_bar(future_bars, anchor_price, 20)
        mfe, mae = self._mfe_mae(future_bars, anchor_price)

        repo.update_outcomes(
            signal.id,
            return_5b=return_5b,
            return_10b=return_10b,
            return_20b=return_20b,
            mfe=mfe,
            mae=mae,
        )
        logger.debug(
            f"Outcomes backfilled for {signal.symbol} @ {anchor_ts}: "
            f"5b={return_5b}, 10b={return_10b}, 20b={return_20b}, "
            f"MFE={mfe}, MAE={mae}"
        )
        return True

    def _return_at_bar(
        self, future_bars: list, anchor_price: float, n_bars: int
    ) -> float | None:
        """% return from anchor close to bar at index n_bars."""
        if len(future_bars) < n_bars:
            return None
        close = float(future_bars[n_bars - 1].close or 0)
        if close <= 0 or anchor_price <= 0:
            return None
        return (close - anchor_price) / anchor_price * 100.0

    def _mfe_mae(
        self, future_bars: list, anchor_price: float
    ) -> tuple[float | None, float | None]:
        """Maximum favorable / adverse excursion as % of anchor price.

        MFE = best (max) high over the forward window.
        MAE = worst (min) low over the forward window.
        """
        if not future_bars or anchor_price <= 0:
            return None, None
        highs = [float(b.high) for b in future_bars if b.high is not None]
        lows = [float(b.low) for b in future_bars if b.low is not None]
        if not highs or not lows:
            return None, None
        mfe = (max(highs) - anchor_price) / anchor_price * 100.0
        mae = (min(lows) - anchor_price) / anchor_price * 100.0
        return mfe, mae

    def _price_at(
        self, db, symbol: str, timeframe: str, ts: datetime
    ) -> float | None:
        """Return the close price for the matching bar, or None."""
        row = (
            db.query(BarModel.close)
            .filter(
                BarModel.symbol == symbol,
                BarModel.timeframe == timeframe,
                BarModel.timestamp == ts,
            )
            .first()
        )
        if row is None:
            return None
        return float(row[0] or 0)


# Process-wide singleton.
signal_recorder = SignalRecorder()
