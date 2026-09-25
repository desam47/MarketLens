"""
Repository for historical signal storage and research queries.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, desc, func, or_, select
from sqlalchemy.orm import Session

from backend.models import BarModel, HistoricalSignal

# Forward windows, in bars of the signal's own timeframe. MFE/MAE span the last one.
OUTCOME_WINDOWS = (5, 10, 20)
OUTCOME_FIELDS = ("return_5b", "return_10b", "return_20b", "mfe", "mae")
DIRECTIONAL_STATES = ("bullish", "bearish")


def outcome_anchor(timeframe: str, timestamp: datetime) -> datetime:
    """Forward bars for an outcome are the bars strictly after this time.

    Daily rows anchor at midnight (see ``SignalRecorder._compute_outcome_for_signal``).
    Queue selection and the outcome calculation must share this rule.
    """
    if timeframe == "1d":
        return timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    return timestamp


def outcome_complete_filter():
    """SQL filter: every 5/10/20-bar return and the 20-bar excursions exist."""
    return and_(*(getattr(HistoricalSignal, field).isnot(None) for field in OUTCOME_FIELDS))


def outcome_pending_filter():
    """SQL filter: at least one forward outcome is still missing."""
    return or_(*(getattr(HistoricalSignal, field).is_(None) for field in OUTCOME_FIELDS))


def is_outcome_complete(signal: Any) -> bool:
    return all(getattr(signal, field) is not None for field in OUTCOME_FIELDS)


def directional_outcome_expr(field: str):
    """SQL counterpart of :func:`directional_outcome`."""
    column = getattr(HistoricalSignal, field)
    bearish_source = {"mfe": HistoricalSignal.mae, "mae": HistoricalSignal.mfe}.get(field, column)
    return case(
        (HistoricalSignal.trend_state == "bullish", column),
        (HistoricalSignal.trend_state == "bearish", -bearish_source),
        else_=None,
    )


def directional_outcome(signal: Any, field: str) -> float | None:
    """A stored raw outcome seen from the signal's own call.

    Stored returns are raw underlying movement. A bearish call earns when
    price falls, so its return is the negated raw return, its favorable
    excursion is the negated raw low-side MAE, and its adverse excursion is
    the negated raw high-side MFE. Neutral and unknown rows made no call and
    have no directional outcome.
    """
    state = getattr(signal, "trend_state", None)
    if state == "bullish":
        return getattr(signal, field)
    if state != "bearish":
        return None
    source = {"mfe": "mae", "mae": "mfe"}.get(field, field)
    value = getattr(signal, source)
    return -value if value is not None else None


def called_it_filter():
    """SQL filter: the 5-bar move went the way the signal called it."""
    return or_(
        and_(HistoricalSignal.trend_state == "bullish", HistoricalSignal.return_5b > 0),
        and_(HistoricalSignal.trend_state == "bearish", HistoricalSignal.return_5b < 0),
    )


def _rounded(value: Any) -> float | None:
    return round(float(value), 4) if value is not None else None


# Display order for per-timeframe coverage rows.
_TIMEFRAME_ORDER = ("1m", "2m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1wk")

# The regime engine only knows the present, so only rows recorded as their bar closed carry one.
REGIME_NOT_RECORDED = "not recorded"


def _next_outcome_window(signal: HistoricalSignal) -> int:
    """How many later bars a pending row needs before recomputing changes it."""
    if signal.return_5b is None:
        return OUTCOME_WINDOWS[0]
    if signal.return_10b is None:
        return OUTCOME_WINDOWS[1]
    return OUTCOME_WINDOWS[2]


class SignalRepository:
    """CRUD + research queries for HistoricalSignal records."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Core CRUD -----------------------------------------------------------

    def create(self, **fields) -> HistoricalSignal:
        """Insert a single HistoricalSignal row."""
        signal = HistoricalSignal(**fields)
        self.db.add(signal)
        self.db.commit()
        self.db.refresh(signal)
        return signal

    def bulk_create(self, records: list[dict[str, Any]]) -> int:
        """Insert multiple HistoricalSignal rows efficiently.

        Returns the number of rows inserted; see :meth:`insert_ignoring_duplicates`.
        """
        inserted = self.insert_ignoring_duplicates(records)
        self.db.commit()
        return inserted

    def insert_ignoring_duplicates(self, records: list[dict[str, Any]]) -> int:
        """Insert rows, skipping any whose (symbol, timeframe, timestamp) already exists.

        The unique index on that triple is what keeps a signal from being
        stored twice when two writers (the recording loop, startup gap-fill,
        a manual request, or the backfill worker process) race past their
        own "already recorded?" checks. A conflict is a safe no-op, not an
        error. Returns the number of rows actually inserted. Does not commit.
        """
        if not records:
            return 0
        dialect = self.db.get_bind().dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        else:
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        # A Core insert on the table reports rowcount; records use ORM attribute
        # names, so map each to its column (``_outcome_missing`` is "outcome_computed").
        attrs = HistoricalSignal.__mapper__.column_attrs
        rows = [{attrs[key].columns[0].name: value for key, value in record.items()} for record in records]
        statement = dialect_insert(HistoricalSignal.__table__).on_conflict_do_nothing(
            index_elements=["symbol", "timeframe", "timestamp"]
        )
        result = self.db.execute(statement, rows)
        return max(int(result.rowcount or 0), 0)

    def get_by_id(self, signal_id: int) -> HistoricalSignal | None:
        return self.db.query(HistoricalSignal).filter(HistoricalSignal.id == signal_id).first()

    def get_latest(self, symbol: str, timeframe: str) -> HistoricalSignal | None:
        """Most recent signal for a symbol/timeframe."""
        return (
            self.db.query(HistoricalSignal)
            .filter(
                HistoricalSignal.symbol == symbol.upper(),
                HistoricalSignal.timeframe == timeframe,
            )
            .order_by(desc(HistoricalSignal.timestamp))
            .first()
        )

    def get_latest_per_timeframe(
        self, symbol: str, timeframes: list[str]
    ) -> dict[str, HistoricalSignal]:
        """Most recent signal for a symbol, one per timeframe.

        Returns a mapping of timeframe -> signal. Timeframes with no
        signal are omitted from the result; callers can detect "no data
        for this symbol" by checking whether the dict is empty.
        """
        symbol = symbol.upper()
        latest: dict[str, HistoricalSignal] = {}
        for tf in timeframes:
            signal = (
                self.db.query(HistoricalSignal)
                .filter(
                    HistoricalSignal.symbol == symbol,
                    HistoricalSignal.timeframe == tf,
                )
                .order_by(desc(HistoricalSignal.timestamp))
                .first()
            )
            if signal is not None:
                latest[tf] = signal
        return latest

    def get_history(
        self,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int = 1000,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        symbols: list[str] | None = None,
        completed_only: bool = False,
    ) -> list[HistoricalSignal]:
        """Historical signal records with optional filters.

        ``symbol`` filters to a single symbol; ``symbols`` filters to any
        of a list. If both are given, ``symbol`` wins (single-symbol query).

        ``completed_only`` skips rows whose full 20-bar outcome is not ready.
        """
        return self._history_query(
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_time,
            end_time=end_time,
            symbols=symbols,
            completed_only=completed_only,
        ).order_by(desc(HistoricalSignal.timestamp)).limit(limit).all()

    def get_history_page(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int = 250,
        offset: int = 0,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        symbols: list[str] | None = None,
        completed_only: bool = False,
    ) -> tuple[list[HistoricalSignal], int]:
        """One deterministic page plus the full matching count for research UI/export."""
        q = self._history_query(
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_time,
            end_time=end_time,
            symbols=symbols,
            completed_only=completed_only,
        )
        total = int(q.order_by(None).count())
        rows = q.order_by(desc(HistoricalSignal.timestamp)).offset(offset).limit(limit).all()
        return rows, total

    def iter_history(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        symbols: list[str] | None = None,
        completed_only: bool = False,
        chunk_size: int = 5_000,
    ):
        """Stream the full matching population for exports without a hidden row cap."""
        return self._history_query(
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_time,
            end_time=end_time,
            symbols=symbols,
            completed_only=completed_only,
        ).order_by(desc(HistoricalSignal.timestamp)).yield_per(chunk_size)

    def _history_query(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        symbols: list[str] | None = None,
        completed_only: bool = False,
    ):
        """Base historical-signal query shared by list, page, and export paths."""
        q = self.db.query(HistoricalSignal)
        if symbol:
            q = q.filter(HistoricalSignal.symbol == symbol.upper())
        elif symbols:
            q = q.filter(HistoricalSignal.symbol.in_([s.upper() for s in symbols]))
        if timeframe:
            q = q.filter(HistoricalSignal.timeframe == timeframe)
        if start_time:
            q = q.filter(HistoricalSignal.timestamp >= start_time)
        if end_time:
            q = q.filter(HistoricalSignal.timestamp <= end_time)
        if completed_only:
            q = q.filter(outcome_complete_filter())
        return q

    def delete_for_symbol(self, symbol: str) -> int:
        """Delete all signal rows for ``symbol`` (all timeframes).

        Called by the watchlist removal path when a symbol leaves every
        active watchlist — bars and signals are both purged so nothing
        orphaned remains in the DB.
        """
        sym = symbol.upper()
        count = (
            self.db.query(HistoricalSignal)
            .filter(HistoricalSignal.symbol == sym)
            .delete(synchronize_session="fetch")
        )
        self.db.commit()
        return count

    # --- Research / outcome queries -----------------------------------------

    def get_signals_needing_outcomes(self, limit: int = 100) -> list[HistoricalSignal]:
        """Pending signals that enough new bars now exist to advance, oldest first.

        A row stays pending until every forward metric exists, including a
        partially matured row that has its 5-bar return but still waits for
        its 10/20-bar outcomes and 20-bar MFE/MAE. It is returned only when
        recomputing it would write something new: when its pair now holds at
        least as many later bars as its next missing window needs.

        Rows that cannot advance yet (a weekly signal waiting 20 weeks, or a
        symbol that no longer receives bars) are skipped instead of refilling
        every batch, so they never block newer rows that can be completed.
        """
        pending = outcome_pending_filter()
        pairs = self.db.query(HistoricalSignal.symbol, HistoricalSignal.timeframe).filter(pending).distinct().all()
        candidates: list[HistoricalSignal] = []
        for symbol, timeframe in pairs:
            newest = [
                row[0]
                for row in self.db.query(BarModel.timestamp)
                .filter(BarModel.symbol == symbol, BarModel.timeframe == timeframe)
                .order_by(BarModel.timestamp.desc())
                .limit(OUTCOME_WINDOWS[-1])
                .all()
            ]
            if len(newest) < OUTCOME_WINDOWS[0]:
                continue
            # The Nth-newest bar is later than a row's anchor exactly when at
            # least N bars follow that anchor.
            cutoffs = {n: newest[n - 1] for n in OUTCOME_WINDOWS if len(newest) >= n}
            bound = cutoffs[OUTCOME_WINDOWS[0]]
            if timeframe == "1d":
                # Daily anchors are midnight; a row later that day shares the anchor.
                bound = outcome_anchor(timeframe, bound) + timedelta(days=1)
            rows = (
                self.db.query(HistoricalSignal)
                .filter(
                    HistoricalSignal.symbol == symbol,
                    HistoricalSignal.timeframe == timeframe,
                    HistoricalSignal.timestamp < bound,
                    pending,
                )
                .order_by(HistoricalSignal.timestamp.asc())
                # Rows that cannot advance are the pair's newest, so a small
                # margin past ``limit`` keeps every advanceable row in reach.
                .limit(limit + OUTCOME_WINDOWS[-1])
                .all()
            )
            for signal in rows:
                cutoff = cutoffs.get(_next_outcome_window(signal))
                if cutoff is not None and outcome_anchor(timeframe, signal.timestamp) < cutoff:
                    candidates.append(signal)
        candidates.sort(key=lambda signal: signal.timestamp)
        return candidates[:limit]

    def update_outcomes(
        self,
        signal_id: int,
        return_5b: float | None,
        return_10b: float | None,
        return_20b: float | None,
        mfe: float | None,
        mae: float | None,
        commit: bool = True,
    ) -> HistoricalSignal | None:
        """Update forward outcomes for a signal row.

        When ``commit=True`` (the default), the session commits immediately.
        Pass ``commit=False`` when batching multiple updates together; the
        caller is responsible for calling ``db.commit()`` once at the end.
        """
        signal = self.get_by_id(signal_id)
        if signal is None:
            return None
        signal.return_5b = return_5b
        signal.return_10b = return_10b
        signal.return_20b = return_20b
        signal.mfe = mfe
        signal.mae = mae
        signal._outcome_missing = not all(
            value is not None
            for value in (return_5b, return_10b, return_20b, mfe, mae)
        )
        if commit:
            self.db.commit()
        self.db.refresh(signal)
        return signal

    def count_by_regime(
        self, symbols: list[str] | None = None, timeframe: str | None = None
    ) -> list[dict[str, Any]]:
        """Count of signals grouped by market regime.

        If ``symbols`` is given, only signals for those symbols are counted;
        ``timeframe`` limits the count to one timeframe.
        """
        q = self.db.query(
            HistoricalSignal.market_regime,
            func.count(HistoricalSignal.id).label("count"),
        ).filter(HistoricalSignal.market_regime.isnot(None))
        if symbols:
            q = q.filter(HistoricalSignal.symbol.in_([s.upper() for s in symbols]))
        if timeframe:
            q = q.filter(HistoricalSignal.timeframe == timeframe)
        rows = q.group_by(HistoricalSignal.market_regime).all()
        return [{"regime": r.market_regime, "count": r.count} for r in rows]

    def get_performance_by_regime(
        self, symbols: list[str] | None = None, timeframe: str | None = None
    ) -> list[dict[str, Any]]:
        """Average forward returns grouped by market regime.

        Only includes signals that have outcomes computed. If ``symbols``
        is given, only signals for those symbols are included. Pass
        ``timeframe``: five bars of 1m and five bars of 1d are different
        horizons, so their returns should not be averaged together.
        """
        q = self.db.query(
            HistoricalSignal.market_regime,
            func.avg(directional_outcome_expr("return_5b")).label("avg_return_5b"),
            func.avg(directional_outcome_expr("return_10b")).label("avg_return_10b"),
            func.avg(directional_outcome_expr("return_20b")).label("avg_return_20b"),
            func.avg(directional_outcome_expr("mfe")).label("avg_mfe"),
            func.avg(directional_outcome_expr("mae")).label("avg_mae"),
            func.count(HistoricalSignal.id).label("count"),
        ).filter(
            HistoricalSignal.market_regime.isnot(None),
            outcome_complete_filter(),
            HistoricalSignal.trend_state.in_(DIRECTIONAL_STATES),
        )
        if symbols:
            q = q.filter(HistoricalSignal.symbol.in_([s.upper() for s in symbols]))
        if timeframe:
            q = q.filter(HistoricalSignal.timeframe == timeframe)
        rows = q.group_by(HistoricalSignal.market_regime).all()
        return [
            {
                "regime": r.market_regime,
                "count": r.count,
                "avg_return_5b": round(float(r.avg_return_5b), 4) if r.avg_return_5b is not None else None,
                "avg_return_10b": round(float(r.avg_return_10b), 4) if r.avg_return_10b is not None else None,
                "avg_return_20b": round(float(r.avg_return_20b), 4) if r.avg_return_20b is not None else None,
                "avg_mfe": round(float(r.avg_mfe), 4) if r.avg_mfe is not None else None,
                "avg_mae": round(float(r.avg_mae), 4) if r.avg_mae is not None else None,
            }
            for r in rows
        ]

    def fetch_excursion_rows(
        self,
        *,
        timeframe: str,
        trend_state: str,
        symbol: str | None = None,
        strength_min: float | None = None,
        strength_max: float | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Any]:
        """Outcome-complete rows for one conditioned slice, for excursion stats.

        Only the columns the distribution needs are selected -- a slice can run
        to six figures when ``symbol`` is omitted, and the percentile math
        (``backend.services.excursion_stats``) reads nothing else. SQLite has no
        ``percentile_cont``, so the quantiles are computed in Python over this
        result rather than in SQL.
        """
        q = self._history_query(
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_time,
            end_time=end_time,
            completed_only=True,
        ).filter(HistoricalSignal.trend_state == trend_state)
        if strength_min is not None:
            q = q.filter(HistoricalSignal.strength >= strength_min)
        if strength_max is not None:
            q = q.filter(HistoricalSignal.strength <= strength_max)
        return (
            q.order_by(None)
            .with_entities(
                HistoricalSignal.trend_state,
                HistoricalSignal.mae,
                HistoricalSignal.mfe,
                HistoricalSignal.return_5b,
                HistoricalSignal.return_10b,
                HistoricalSignal.return_20b,
            )
            .all()
        )

    def get_signals_by_trend_state(
        self, trend_state: str, limit: int = 100
    ) -> list[HistoricalSignal]:
        """Signals with a specific trend state."""
        return (
            self.db.query(HistoricalSignal)
            .filter(HistoricalSignal.trend_state == trend_state)
            .order_by(desc(HistoricalSignal.timestamp))
            .limit(limit)
            .all()
        )

    def get_signal_count(self) -> int:
        """Total count of historical signals."""
        return self.db.query(func.count(HistoricalSignal.id)).scalar() or 0

    def research_summary(
        self,
        *,
        timeframe: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        """Aggregate research metrics over the whole filtered population.

        Coverage (recorded and complete rows per timeframe) is always
        returned. Performance is returned only for a single ``timeframe``:
        a 5-bar outcome means five minutes on 1m and five sessions on 1d,
        so pooling them would average different horizons. Every return is
        direction-adjusted and uses complete bullish/bearish outcomes only.
        """
        base = self._history_query(
            timeframe=timeframe, start_time=start_time, end_time=end_time, symbols=symbols
        ).order_by(None)
        complete = outcome_complete_filter()
        coverage_rows = (
            base.with_entities(
                HistoricalSignal.timeframe,
                func.count(HistoricalSignal.id),
                func.sum(case((complete, 1), else_=0)),
            )
            .group_by(HistoricalSignal.timeframe)
            .all()
        )
        coverage = sorted(
            (
                {"timeframe": tf, "recorded": int(recorded), "complete": int(done or 0)}
                for tf, recorded, done in coverage_rows
            ),
            key=lambda row: (
                _TIMEFRAME_ORDER.index(row["timeframe"])
                if row["timeframe"] in _TIMEFRAME_ORDER
                else len(_TIMEFRAME_ORDER),
                row["timeframe"],
            ),
        )
        recorded = sum(row["recorded"] for row in coverage)
        completed = sum(row["complete"] for row in coverage)
        with_regime = (
            base.filter(complete, HistoricalSignal.market_regime.isnot(None))
            .with_entities(func.count(HistoricalSignal.id))
            .scalar()
        ) or 0
        summary: dict[str, Any] = {
            "recorded": recorded,
            "complete": completed,
            "timeframe_coverage": coverage,
            "regime_coverage": {"with_regime": int(with_regime), "complete": completed},
            "performance": None,
        }
        if timeframe:
            overall = self._group_metrics(base, None)
            summary["performance"] = {
                **(overall[0] if overall else self._empty_metrics("all")),
                "by_regime": self._group_metrics(
                    base, func.coalesce(HistoricalSignal.market_regime, REGIME_NOT_RECORDED)
                ),
                "by_trend": self._group_metrics(
                    base, func.coalesce(HistoricalSignal.trend_state, "unknown")
                ),
            }
        return summary

    @staticmethod
    def _empty_metrics(label: str) -> dict[str, Any]:
        return {
            "label": label,
            "complete": 0,
            "directional": 0,
            "win_rate": None,
            "avg_signal_return_5b": None,
            "avg_signal_return_10b": None,
        }

    def _group_metrics(self, base, label_expr) -> list[dict[str, Any]]:
        """Directional metrics for ``base``, grouped by ``label_expr`` (or overall when None)."""
        complete = outcome_complete_filter()
        directional = and_(complete, HistoricalSignal.trend_state.in_(DIRECTIONAL_STATES))
        columns = [
            func.sum(case((complete, 1), else_=0)),
            func.sum(case((directional, 1), else_=0)),
            func.sum(case((and_(directional, called_it_filter()), 1), else_=0)),
            func.avg(case((directional, directional_outcome_expr("return_5b")), else_=None)),
            func.avg(case((directional, directional_outcome_expr("return_10b")), else_=None)),
        ]
        if label_expr is None:
            rows = [("all", *base.with_entities(*columns).one())]
        else:
            rows = base.with_entities(label_expr, *columns).group_by(label_expr).all()
        metrics = []
        for label, done, n_directional, wins, avg5, avg10 in rows:
            if not done:
                continue
            n_directional = int(n_directional or 0)
            metrics.append({
                "label": label,
                "complete": int(done),
                "directional": n_directional,
                "win_rate": round(int(wins or 0) / n_directional, 4) if n_directional else None,
                "avg_signal_return_5b": _rounded(avg5),
                "avg_signal_return_10b": _rounded(avg10),
            })
        return sorted(metrics, key=lambda row: (-row["complete"], str(row["label"])))

    def get_stats(self, symbol: str, timeframe: str | None = None) -> dict[str, Any]:
        """Track-record statistics for one symbol (optionally one timeframe).

        ``total``                every stored signal.
        ``with_outcomes``        those whose full 5/10/20-bar outcome is complete.
        ``directional_outcomes`` complete bullish or bearish signals; the rest below use only these.
        ``avg_return_5b`` / ``avg_return_10b``   mean direction-adjusted return over the next
                                 5 / 10 bars: a bearish call earns when price falls. None if none.
        ``win_rate``             share of those calls that were right: a bullish one wins when its
                                 5-bar return is positive, a bearish one when it is negative.

        Stored returns are raw underlying movement. Averaging them directly would report a
        bearish call that worked as a loss and let neutral rows, which made no call, move the
        average, so every figure here is computed against each signal's own ``trend_state``.
        """
        q = self.db.query(HistoricalSignal).filter(HistoricalSignal.symbol == symbol.upper())
        if timeframe:
            q = q.filter(HistoricalSignal.timeframe == timeframe)
        complete = outcome_complete_filter()
        directional = and_(complete, HistoricalSignal.trend_state.in_(DIRECTIONAL_STATES))
        called_it = called_it_filter()
        row = q.with_entities(
            func.count(HistoricalSignal.id),
            func.sum(case((complete, 1), else_=0)),
            func.sum(case((directional, 1), else_=0)),
            func.avg(case((directional, directional_outcome_expr("return_5b")), else_=None)),
            func.avg(case((directional, directional_outcome_expr("return_10b")), else_=None)),
            func.sum(case((and_(directional, called_it), 1), else_=0)),
        ).one()
        total, with_outcomes, n_directional, avg5, avg10, n_wins = row
        return {
            "total": int(total or 0),
            "with_outcomes": int(with_outcomes or 0),
            "directional_outcomes": int(n_directional or 0),
            "avg_return_5b": round(float(avg5), 4) if avg5 is not None else None,
            "avg_return_10b": round(float(avg10), 4) if avg10 is not None else None,
            "win_rate": round(int(n_wins or 0) / int(n_directional), 4) if n_directional else None,
        }


# Signals are derived from bars, and startup "signal hygiene" (backend/api/main_helpers.py) compares
# each timeframe's bar count with its signal count and regenerates signals whenever bars outnumber
# them. So a timeframe's signals must outlive its bars by a margin: pruned to the bar window
# exactly, ordinary clock skew between the two hourly prunes would open a small gap and trigger a
# full re-backfill.
SIGNAL_RETENTION_MARGIN_DAYS = 2


def prune_signals_by_retention(
    db: Session,
    chunk_size: int = 5000,
    now: datetime | None = None,
) -> dict[str, int]:
    """Delete each timeframe's signals once they are older than that timeframe's BAR retention
    window (``settings.retention``: 16 days for 1m-30m, 366 for 1h/4h, 1096 for 1d/1wk) plus
    ``SIGNAL_RETENTION_MARGIN_DAYS``.

    Before this, signals had no retention at all: about 47,000 rows a day (roughly 30 MB), so
    ~10 GB a year, and intraday signals outlived the bars they were computed from by months.
    Deletes in short chunked transactions so the SQLite write lock is never held for long.
    Returns ``{timeframe: rows_deleted}`` for timeframes that lost rows.
    """
    from backend.config.settings import settings
    from backend.utils.timezone import now_ny

    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be > 0, got {chunk_size}")
    now = now or now_ny()
    timeframes = [row[0] for row in db.query(HistoricalSignal.timeframe).distinct().all()]
    deleted_by_tf: dict[str, int] = {}
    for tf in timeframes:
        days = settings.retention.days_for(tf) + SIGNAL_RETENTION_MARGIN_DAYS
        cutoff = now - timedelta(days=days)
        total = 0
        while True:
            ids = (
                select(HistoricalSignal.id)
                .where(HistoricalSignal.timeframe == tf, HistoricalSignal.timestamp < cutoff)
                .limit(chunk_size)
            )
            deleted = (
                db.query(HistoricalSignal)
                .filter(HistoricalSignal.id.in_(ids))
                .delete(synchronize_session=False)
            )
            db.commit()
            total += deleted
            if deleted < chunk_size:
                break
        if total:
            deleted_by_tf[tf] = total
    return deleted_by_tf


def delete_signals_for_symbol(symbol: str) -> int:
    """Delete every signal row for ``symbol`` from the DB.

    Returns the number of rows deleted. Use this when a symbol is removed
    from every watchlist and we no longer want any historical signals for it.
    """
    from backend.database import SessionLocal  # avoid circular import

    if not symbol:
        return 0
    db = SessionLocal()
    try:
        repo = SignalRepository(db)
        return repo.delete_for_symbol(symbol)
    finally:
        db.close()
