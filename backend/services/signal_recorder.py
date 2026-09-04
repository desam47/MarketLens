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
import bisect
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
        # Store as naive America/New_York (EDT/EST) to match the bar table
        # convention (2026-09-02+). The signal API serializes these with an
        # explicit ``-04:00``/``-05:00`` suffix so the browser parses them
        # correctly regardless of local timezone.
        from backend.utils.timezone import to_ny, now_ny

        if timestamp is None:
            ts = now_ny()
        else:
            ts = to_ny(timestamp)

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

    def backfill_outcomes(self, batch_size: int = 1000) -> int:
        """Compute forward outcomes for signals that have enough future data.

        Returns the number of signals updated. Safe to call on a schedule
        (e.g. every 90s from the ingestion service) — it only touches
        rows that have at least REQUIRED_FORWARD_BARS future bars stored.

        Performance (Phase 3.x optimization):
          - 1 query to fetch candidate signals
          - 1 query to bulk-pre-fetch all future bars for the batch
            (vs N+1 — one query per candidate)
          - Binary search locates each candidate's bars in the pre-fetched
            (symbol, timeframe) bucket
          - Single commit at the end of the batch
            (vs one commit per candidate)
        """
        db = SessionLocal()
        updated = 0
        try:
            repo = SignalRepository(db)
            candidates = repo.get_signals_needing_outcomes(limit=batch_size)
            if not candidates:
                return 0

            # Resolve anchor prices for any candidates missing a price.
            # Bulk-load the (symbol, timeframe, ts) → close lookup in one
            # query rather than per-signal _price_at() round-trips.
            self._bulk_fill_anchor_prices(db, candidates)

            # Bulk pre-fetch of all future bars for all (symbol, timeframe)
            # pairs in the candidate set, starting from the earliest anchor
            # so we can serve every signal in the batch.
            future_bars_by_pair = self._bulk_prefetch_future_bars(
                db, candidates
            )

            # Compute outcomes in Python; persist via a single commit.
            for signal in candidates:
                if self._compute_outcome_for_signal(
                    signal, repo, future_bars_by_pair
                ):
                    updated += 1
            db.commit()
        except Exception as e:
            logger.error(f"Outcome backfill failed: {e}")
            db.rollback()
        finally:
            db.close()
        return updated

    # --- Bulk helpers for backfill_outcomes --------------------------------

    def _bulk_fill_anchor_prices(
        self,
        db,
        candidates: list[HistoricalSignal],
    ) -> None:
        """Fill missing ``signal.price`` for candidates in one query.

        Mutates each candidate in place. Candidates with a valid price
        already set are left alone. For 1d signals, the anchor timestamp
        is normalized to midnight to match the canonical daily bar.
        """
        missing: list[HistoricalSignal] = [
            s for s in candidates
            if s.price is None or s.price <= 0
        ]
        if not missing:
            return

        # Group by (symbol, timeframe) so we can issue one query per pair.
        # This is still fewer round-trips than the previous per-signal
        # _price_at() approach (which was N queries for N missing prices).
        from collections import defaultdict
        by_pair: dict[tuple[str, str], list[HistoricalSignal]] = defaultdict(list)
        for s in missing:
            ts = s.timestamp
            if s.timeframe == "1d":
                ts = ts.replace(hour=0, minute=0, second=0, microsecond=0)
            by_pair[(s.symbol, s.timeframe)].append(s)
            # Stash the normalized anchor so the bulk-fetch can reuse it
            # without recomputing the 1d midnight normalization below.
            s._bulk_anchor_ts = ts  # type: ignore[attr-defined]

        for (symbol, timeframe), sigs in by_pair.items():
            ts_set = {s._bulk_anchor_ts for s in sigs}  # type: ignore[attr-defined]
            rows = (
                db.query(BarModel.timestamp, BarModel.close)
                .filter(
                    BarModel.symbol == symbol,
                    BarModel.timeframe == timeframe,
                    BarModel.timestamp.in_(ts_set),
                )
                .all()
            )
            close_by_ts = {row.timestamp: float(row.close or 0) for row in rows}
            for s in sigs:
                close = close_by_ts.get(s._bulk_anchor_ts)  # type: ignore[attr-defined]
                if close and close > 0:
                    s.price = close

    def _bulk_prefetch_future_bars(
        self,
        db,
        candidates: list[HistoricalSignal],
    ) -> dict[tuple[str, str], list[BarModel]]:
        """Fetch all future bars for the candidate set in one query.

        Returns a dict keyed by (symbol, timeframe) → sorted list of
        BarModel rows with ``timestamp`` strictly after the earliest
        anchor in that pair. The caller uses ``bisect`` to find each
        candidate's 25-bar window from this single fetch.
        """
        if not candidates:
            return {}

        from collections import defaultdict
        # Track the earliest anchor per (symbol, timeframe) so the bulk
        # query grabs everything the batch will ever need.
        earliest_anchor: dict[tuple[str, str], datetime] = {}
        for s in candidates:
            anchor = s.timestamp
            if s.timeframe == "1d":
                anchor = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
            key = (s.symbol, s.timeframe)
            existing = earliest_anchor.get(key)
            if existing is None or anchor < existing:
                earliest_anchor[key] = anchor

        # Build OR conditions for (symbol=X AND timeframe=Y AND timestamp > anchor)
        # grouped by pair. SQLAlchemy doesn't accept a tuple_ IN easily across
        # SQLite + Postgres, so we OR the per-pair clauses.
        pair_clauses = []
        for (symbol, timeframe), anchor in earliest_anchor.items():
            pair_clauses.append(
                and_(
                    BarModel.symbol == symbol,
                    BarModel.timeframe == timeframe,
                    BarModel.timestamp > anchor,
                )
            )
        from sqlalchemy import or_
        rows = (
            db.query(BarModel)
            .filter(or_(*pair_clauses))
            .order_by(
                BarModel.symbol.asc(),
                BarModel.timeframe.asc(),
                BarModel.timestamp.asc(),
            )
            .all()
        )

        # Bucket by (symbol, timeframe) for the binary search.
        buckets: dict[tuple[str, str], list[BarModel]] = defaultdict(list)
        for bar in rows:
            buckets[(bar.symbol, bar.timeframe)].append(bar)
        return buckets

    def record_from_recent_bars(
        self, symbols: list[str]
    ) -> int:
        """Walk the latest stored bar per (symbol, timeframe) and record signals.

        Phase 3.1 note: the bars table only stores 1m bars. The subquery
        queries all ``timeframe`` values that have rows for the given symbols,
        so signals are recorded for whatever timeframes are available — not
        just the ingestion timeframes. If higher-TF bars are backfilled later
        (e.g. via ``backfill_1m.py`` and provider fetch at 1d), signals for
        those timeframes will automatically appear.

        Returns the count of new signals written.
        """
        if not symbols:
            return 0
        recorded = 0
        db = SessionLocal()
        try:
            # Find the most recent bar per (symbol, timeframe) in one query.
            # Note: ``BarModel.timeframe`` is included in the GROUP BY so we
            # get one row per (symbol, timeframe) pair — not just one per symbol.
            # Before Phase 3.1: the table had 1m/5m/15m/30m/1h/1d/1wk bars.
            # After Phase 3.1: only 1m bars exist; higher-TF signals are only
            # recorded once backfill_1m.py + provider fetch adds those bars.
            subq = (
                db.query(
                    BarModel.symbol,
                    BarModel.timeframe,
                    func.max(BarModel.timestamp).label("ts"),
                )
                .filter(BarModel.symbol.in_([s.upper() for s in symbols]))
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

    def backfill_signals_for_symbol(
        self,
        symbol: str,
        timeframe: str | None = None,
        max_bars: int = 50000,
    ) -> int:
        """Record signals for all stored bars of ``symbol`` (not just the latest).

        Use this when a symbol is first added to the watchlist — it walks
        the full backfilled history and writes a signal row for every bar
        that doesn't already have one. Dedup is by
        (symbol, timeframe, timestamp) so re-runs are no-ops.

        ``max_bars`` caps the work per call to avoid blowing the request
        budget. The default of 50 000 covers ~6 months of 1m bars at
        typical trading density (≈ 250 bars/day × 6 × 21 ≈ 31 500 bars).
        For shorter timeframes with denser data (1m), the high limit
        ensures the full history is captured in one shot. The caller can
        also invoke this in a loop for very long histories (years of data).

        When called with ``timeframe=None``, iterates **per timeframe** so
        every stored timeframe gets its own ``max_bars`` budget. Without
        this, the cross-TF query is dominated by 1m bars (which arrive
        last) and higher-TF bars (1h, 1d) get starved — leaving the
        historical_signals table missing entries for 1d/1h even when
        bars exist for those timeframes.

        Performance (Phase 3.x optimization):
          - 1 query to fetch existing signal timestamps for dedup
            (vs N — one per bar in the legacy per-bar path)
          - bulk_save_objects() inserts all new signals in one round-trip
          - single commit per (symbol, timeframe) pair
            (vs one commit per bar in the legacy per-bar path)
        """
        sym = symbol.upper()
        recorded = 0
        db = SessionLocal()
        try:
            if timeframe is not None:
                timeframes = [timeframe]
            else:
                # Iterate per timeframe so each gets its own max_bars budget.
                timeframes = [
                    tf for (tf,) in
                    db.query(BarModel.timeframe)
                      .filter(BarModel.symbol == sym)
                      .distinct()
                      .all()
                ]
                # Order timeframes so shorter (denser) ones go first — they
                # produce the most rows for the budget. 1d/1wk go last so
                # the cap doesn't get eaten by sub-hour bars.
                order = ["1m", "2m", "3m", "5m", "15m", "30m",
                         "1h", "4h", "1d", "1wk"]
                timeframes.sort(key=lambda t: order.index(t) if t in order else 99)

            for tf in timeframes:
                recorded += self._bulk_record_bars(db, sym, tf, max_bars)
        except Exception as e:
            logger.error(f"backfill_signals_for_symbol({sym}) failed: {e}")
            db.rollback()
        finally:
            db.close()
        logger.info(
            f"backfill_signals_for_symbol({sym}): recorded {recorded} new signals"
        )
        return recorded

    def _bulk_record_bars(
        self,
        db,
        symbol: str,
        timeframe: str,
        max_bars: int,
    ) -> int:
        """Bulk-record signals for one (symbol, timeframe) using dedup set + bulk insert.

        Strategy (Phase 3.x optimization):
          1. Fetch the (symbol, timeframe) bars (capped at max_bars).
          2. Fetch the existing signal timestamps for that pair in one
             ``IN()``-style query and build an O(1) dedup set.
          3. For each bar not in the set, build the signal record and
             collect into a list. No DB I/O in the loop.
          4. Single ``bulk_save_objects()`` + single ``db.commit()`` for
             the whole batch. In-process dedup cache is populated
             post-commit so subsequent live ingestion also sees them.

        Returns the number of new signals written.
        """
        bars = (
            db.query(BarModel)
            .filter(BarModel.symbol == symbol, BarModel.timeframe == timeframe)
            .order_by(BarModel.timestamp.desc())
            .limit(max_bars)
            .all()
        )
        if not bars:
            return 0

        # Step 1: bulk-fetch existing signal timestamps for this pair.
        # Note: the bars query is desc; we need an asc-ordered list of
        # timestamps for the dedup set. Pull only the timestamp column.
        existing_ts = {
            ts for (ts,) in db.query(HistoricalSignal.timestamp)
            .filter(
                HistoricalSignal.symbol == symbol,
                HistoricalSignal.timeframe == timeframe,
            )
            .all()
        }

        # Step 2: build the per-bar signal records in Python (no DB I/O).
        new_records: list[dict] = []
        for bar in bars:
            ts = bar.timestamp
            if ts in existing_ts:
                continue
            # The in-process cache guards against rapid duplicate calls
            # within a single process (e.g. two concurrent ingestion
            # ticks). On a hot reload it can be stale, so the DB dedup
            # above is the source of truth.
            cache_key = (symbol, timeframe, ts)
            if cache_key in self._last_recorded:
                continue

            trend_state = self._classify_trend_from_bar(bar)
            trend_score = self._score_from_bar(bar)
            volume_state = self._classify_volume(bar)
            market_regime = self._get_market_regime()

            new_records.append({
                "symbol": symbol,
                "timestamp": ts,
                "timeframe": timeframe,
                "price": float(bar.close or 0),
                "trend_score": trend_score,
                "trend_state": trend_state,
                "strength": min(abs(trend_score) / 100.0, 1.0) if trend_score is not None else None,
                "market_regime": market_regime,
                "relative_strength": None,
                "sector_alignment": None,
                "volume_state": volume_state,
                "momentum": trend_score,
                "structure": trend_state,
                "confidence_inputs": json.dumps({
                    "bar_close": float(bar.close or 0),
                    "bar_high": float(bar.high or 0),
                    "bar_low": float(bar.low or 0),
                }),
                "strategy_version": "v1",
                "data_quality": "good",
                "_outcome_missing": True,
            })
            # Mark in the cache so the post-commit in-process cache
            # can be populated without an extra query.
            self._last_recorded[cache_key] = ts

        if not new_records:
            return 0

        # Step 3: bulk insert. SQLAlchemy's bulk_save_objects skips the
        # unit-of-work per-row overhead, and we commit once at the end.
        # We pass return_defaults=False because the autoincrement PK
        # isn't needed by the caller (we return count).
        objects = [HistoricalSignal(**r) for r in new_records]
        try:
            db.bulk_save_objects(objects, return_defaults=False)
            db.commit()
        except Exception as e:
            db.rollback()
            # Roll back the in-process cache so a retry can repopulate.
            for r in new_records:
                self._last_recorded.pop(
                    (r["symbol"], r["timeframe"], r["timestamp"]), None
                )
            logger.error(
                f"_bulk_record_bars({symbol}, {timeframe}, n={len(new_records)}) "
                f"failed: {e}"
            )
            raise
        return len(new_records)

    def _record_from_bar(self, bar, db) -> bool:
        """Build a snapshot for a single bar and write a signal row.

        Returns True if a new signal was persisted.
        """
        # Skip webull :30 noise in 1h — Webull returns 1h bars at :30 offsets
        # (09:30, 10:30...) instead of the standard :00 boundaries. These
        # are not valid hour-close bars.
        if bar.timeframe == "1h" and bar.timestamp.minute == 30:
            return False

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
        """Classify a bar as bullish/bearish/neutral.

        Primary path: read the in-process trend engine's current signal for
        (symbol, timeframe). The engine has 200 bars of seeded history plus
        live ticks, so EMA/RSI/MACD/ADX/SuperTrend/Bollinger/ROC all
        contribute — far more reliable than a 1-bar heuristic.

        Fallback: close-vs-open when the engine is unavailable (e.g. for
        timeframes not registered in the trend engine, or during the
        very first bars before the engine has any signals).
        """
        engine_signal = self._get_trend_signal(bar.symbol, bar.timeframe)
        if engine_signal is not None and engine_signal.score is not None:
            score = engine_signal.score
            if score >= 30:
                return "bullish"
            if score <= -30:
                return "bearish"
            return "neutral"
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
        """Build a -100..+100 score.

        Primary path: read from the in-process trend engine's current
        signal (weighted EMA+RSI+MACD+ADX+SuperTrend+BB+ROC composite).

        Fallback: pct move from open to close, scaled 5× (so 0.2% move
        → ±1 score).
        """
        engine_signal = self._get_trend_signal(bar.symbol, bar.timeframe)
        if engine_signal is not None and engine_signal.score is not None:
            return float(engine_signal.score)
        try:
            close = float(bar.close or 0)
            open_ = float(bar.open or close)
            if open_ <= 0:
                return 0.0
            pct = (close - open_) / open_ * 100.0
            return max(min(pct * 5.0, 100.0), -100.0)
        except Exception:
            return 0.0

    def _get_trend_signal(self, symbol: str, timeframe: str):
        """Look up the current trend signal from the in-process registry.

        Returns ``None`` if the engine isn't initialized or has no signal
        yet for the requested (symbol, timeframe). Lazy-imports the
        registry to avoid a circular import at module load.
        """
        try:
            from backend.api.trend.registry import get_engine
            from backend.engines.timeframe import Timeframe

            tf = Timeframe(timeframe)
            engine = get_engine(symbol)
            return engine.get_current_trend(tf)
        except Exception:
            return None

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
        self,
        signal: HistoricalSignal,
        repo: SignalRepository,
        future_bars_by_pair: dict[tuple[str, str], list[BarModel]] | None = None,
    ) -> bool:
        """Compute forward outcomes for one signal row.

        Returns True if outcomes were updated, False if the row was
        left alone (insufficient future data).

        When ``future_bars_by_pair`` is provided, future bars are sliced
        from the pre-fetched (symbol, timeframe) bucket using ``bisect``
        — no DB round-trip. When it's ``None``, the function falls back
        to the original per-signal query (used by the legacy direct
        callers and tests).
        """
        db = repo.db
        anchor_price = signal.price
        if anchor_price is None or anchor_price <= 0:
            # Try to recover the price from the matching bar. The bulk
            # path normally fills this in advance; this fallback covers
            # direct callers (tests) and any rows the bulk pass missed.
            anchor_price = self._price_at(db, signal.symbol, signal.timeframe, signal.timestamp)
            if anchor_price is None or anchor_price <= 0:
                logger.debug(
                    f"Cannot compute outcomes for signal {signal.id}: "
                    f"no price at anchor"
                )
                return False
            signal.price = anchor_price

        # Pull the next REQUIRED_FORWARD_BARS bars in the same timeframe as
        # the signal. For a 1d signal these are 1d bars (5/10/20-day returns);
        # for a 1m signal they are 1m bars (5/10/20-minute returns).
        #
        # Special case for 1d: the 1d bar table currently mixes two kinds of
        # rows — proper midnight (00:00) daily bars and 13:30 intraday
        # snapshots. The latter leak in via the Alpaca provider's free-tier
        # feed and were backfilled alongside the 1d series. To keep the
        # forward-outcome math aligned to calendar days, normalize the anchor
        # to midnight: future bars are "anything strictly after the prior
        # midnight", which excludes same-day 13:30 noise.
        if signal.timeframe == "1d":
            anchor_ts = signal.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            anchor_ts = signal.timestamp

        if future_bars_by_pair is not None:
            pair = (signal.symbol, signal.timeframe)
            bucket = future_bars_by_pair.get(pair)
            if bucket is None:
                future_bars = []
            else:
                # Bucket is sorted ascending by timestamp (guaranteed by the
                # bulk query's ORDER BY). Binary-search for the first row
                # strictly greater than anchor_ts.
                keys = [b.timestamp for b in bucket]
                idx = bisect.bisect_right(keys, anchor_ts)
                future_bars = bucket[idx:idx + REQUIRED_FORWARD_BARS]
        else:
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

        if not future_bars:
            # No data after the signal at all — leave the row alone.
            return False

        # Compute whatever windows are available now; the missing ones stay
        # None and get filled in by future backfill runs as more bars arrive.
        # This used to require len >= 20 (== 20b window) which left a 19-bar
        # blind spot at the data edge — signals 20 days back from the edge
        # were stuck with NULL until the 20th day, even though their 5b/10b
        # windows were already computable.
        return_5b = self._return_at_bar(future_bars, anchor_price, 5)
        return_10b = self._return_at_bar(future_bars, anchor_price, 10)
        return_20b = self._return_at_bar(future_bars, anchor_price, 20)
        mfe, mae = self._mfe_mae(future_bars, anchor_price)

        # Only skip if even MFE/MAE are None — that means there are no
        # future bars at all (the "zero future bars" case already returned
        # False above, but this guards against the rare 0-bar query result).
        if mfe is None and mae is None:
            return False

        # Direct ORM attribute mutation (no per-signal commit). The bulk
        # caller commits once at the end of the batch; the legacy direct
        # caller (used by tests) commits via repo.update_outcomes below.
        if future_bars_by_pair is not None:
            signal.return_5b = return_5b
            signal.return_10b = return_10b
            signal.return_20b = return_20b
            signal.mfe = mfe
            signal.mae = mae
            signal._outcome_missing = False
        else:
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
        """Return the close price for the matching bar, or None.

        For 1d, normalise the timestamp to midnight so we look up the
        canonical daily bar rather than the same-day 13:30 intraday
        snapshot that may also be in the table.
        """
        if timeframe == "1d":
            ts = ts.replace(hour=0, minute=0, second=0, microsecond=0)
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
