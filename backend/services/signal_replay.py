"""
Point-in-time trend labels for stored bars.

A historical signal must say what the trend was AT THAT BAR. The recorder used to stamp every
backfilled bar with the live engine's *current* score, so all of a backfill's rows carried one
identical label (SPY's 754 daily signals were 100% neutral, AAPL's 99.6% bullish) and every
win-rate computed from them measured nothing.

``replay_trend_scores`` feeds the stored bars, oldest first, through a private ``TrendEngine`` --
the same one, fed the same way, as the live seeder -- and records the score it reports after each
bar. Nothing after a bar can influence that bar's score, so there is no look-ahead.

The first ``REPLAY_WARMUP_BARS`` bars are not labelled: the indicator stack (EMA-200 and friends)
is still converging and its score there depends on where the replay happened to start.
"""
import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

# Bars fed to the engine before its score is trusted. This is the depth the live seeder gives its
# engines, and on daily bars (SPY, AAPL) a replay started 200 bars earlier reproduces the
# full-history score to within 0.03. Intraday scores never fully converge -- SuperTrend is a state
# machine and relative volume needs days of baseline -- so an intraday label is "the score with
# all the stored history before the bar", which is causal and repeatable but is not what the live
# engine, seeded with 200 bars, happened to show at the time.
REPLAY_WARMUP_BARS = 200

# Score thresholds shared with the live recorder's classification.
BULLISH_AT = 30.0
BEARISH_AT = -30.0

_NO_LABEL: dict[str, Any] = {
    "trend_score": None, "trend_state": None, "strength": None, "momentum": None, "structure": None,
    "market_regime": None,
}


def state_from_score(score: float) -> str:
    if score >= BULLISH_AT:
        return "bullish"
    if score <= BEARISH_AT:
        return "bearish"
    return "neutral"


def label_columns(score: float | None) -> dict[str, Any]:
    """The signal-row columns a trend score determines.

    ``market_regime`` is always None: the regime engine only knows *now*, and stamping today's
    regime on a past bar is the same lie as stamping today's score. ``score`` None means "no
    point-in-time value" (warm-up, unsupported timeframe, no bar) and yields an all-None label
    rather than a made-up neutral.
    """
    if score is None:
        return dict(_NO_LABEL)
    state = state_from_score(score)
    return {
        "trend_score": score,
        "trend_state": state,
        "strength": min(abs(score) / 100.0, 1.0),
        "momentum": score,
        "structure": state,
        "market_regime": None,
    }


# What a replay engine keeps, so that one held per (symbol, timeframe) stays small. All three cuts
# leave every score bit-for-bit unchanged (checked on daily, hourly, 5m and 1m bars): the
# indicators are updated incrementally and never re-read old candles or old values.
#   * the aggregator appends every candle it builds and never trims (~6 KB per bar);
_CANDLES_KEPT = 100
#   * every indicator appends every value it computes and never trims;
_VALUES_KEPT = 500
#   * the engine builds an indicator stack for ALL ten timeframes and updates each from the
#     candles of its own timeframe, but a replay only ever reads one. Dropping the other nine
#     takes a daily engine from 41 MB to 1 MB and halves the time per bar.

# How long a bar's candle is open, by timeframe. Bars are stamped with their OPEN time.
_BAR_MINUTES = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120,
                "4h": 240, "1d": 1440, "1wk": 10080}


def bar_length(timeframe: str) -> timedelta:
    """How long a candle of ``timeframe`` is open; zero for a timeframe of unknown length, whose
    bars are taken to have closed the moment they opened."""
    return timedelta(minutes=_BAR_MINUTES.get(timeframe, 0))


def bar_end(bar_ts: datetime, timeframe: str) -> datetime:
    """When the candle stamped ``bar_ts`` (naive New York open time) closes."""
    return bar_ts + bar_length(timeframe)


def is_closed(bar_ts: datetime, timeframe: str, now: datetime | None = None) -> bool:
    """Whether the bar can no longer change.

    The engine cannot un-see a tick, so a still-forming bar must never be fed to it: every score
    after that would carry a half-finished candle.
    """
    if now is None:
        from backend.utils.timezone import now_ny
        now = now_ny()
    return bar_end(bar_ts, timeframe) <= now


class BarReplay:
    """One private ``TrendEngine`` fed closed bars, oldest first, one at a time.

    ``feed`` returns the score after that bar. Feeding the whole history in one go
    (``replay_trend_scores``) and feeding it bar by bar as bars close (the live recorder) run
    the same code, so a stored signal means the same thing whichever way it was written.
    """

    def __init__(self, symbol: str, timeframe: str, warmup: int = REPLAY_WARMUP_BARS) -> None:
        from backend.engines.timeframe import (
            Timeframe,
            TimeframeEngine,
            multi_symbol_timeframe_engine,
        )
        from backend.trend.trend_engine import TrendEngine

        self.symbol, self.timeframe, self.warmup = symbol, timeframe, warmup
        self.fed = 0
        self.last_ts: datetime | None = None
        self._engine = None
        try:
            self._tf = Timeframe(timeframe)
        except ValueError:
            return  # a timeframe the engine does not support: every bar scores None
        # TrendEngine attaches to the PROCESS-WIDE per-symbol candle aggregator, and resets its
        # candle bookkeeping. Under the real symbol, years of old ticks would land in the live
        # engine's candles. So build it under a throwaway name whose aggregator is created here
        # (empty: TrendEngine would otherwise seed a new one with a price-0 tick at "now").
        # The engine keeps its own reference, so the registry entry is dropped straight away.
        scratch = f"__replay__{symbol}_{timeframe}_{uuid4().hex[:8]}"
        multi_symbol_timeframe_engine.engines[scratch] = TimeframeEngine(scratch)
        try:
            self._engine = TrendEngine(scratch)
        finally:
            multi_symbol_timeframe_engine.engines.pop(scratch, None)
        self._engine.indicators = {self._tf: self._engine.indicators[self._tf]}

    def feed(self, bar: Any) -> float | None:
        """Feed one closed bar (``timestamp``, ``close``, ``volume``); its score, or None."""
        from backend.utils.timezone import ny_to_utc

        index = self.fed
        self.fed += 1
        self.last_ts = bar.timestamp
        if self._engine is None:
            return None
        score = None
        try:
            self._engine.update(
                price=float(bar.close or 0.0), volume=int(bar.volume or 0),
                timestamp=bar.timestamp, only_timeframe=self._tf,
            )
            signal = self._engine.get_current_trend(self._tf)
            # ``get_current_trend`` is the LAST signal ever emitted: unless it is this bar's,
            # the engine produced nothing for it and the score belongs to an earlier bar.
            if (signal is not None and signal.score is not None and index >= self.warmup
                    and signal.timestamp == ny_to_utc(bar.timestamp)):
                score = float(signal.score)
        except Exception:  # noqa: BLE001 - one unscorable bar must not sink the replay
            logger.debug("replay %s/%s: bar %s unscorable", self.symbol, self.timeframe,
                         bar.timestamp, exc_info=True)
        for candles in self._engine.timeframe_engine.candles.values():
            if len(candles) > _CANDLES_KEPT:
                del candles[:-_CANDLES_KEPT]
        for indicator in self._engine.indicators[self._tf].values():
            if len(indicator.values) > _VALUES_KEPT:
                del indicator.values[:-_VALUES_KEPT]
            if len(indicator.timestamps) > _VALUES_KEPT:
                del indicator.timestamps[:-_VALUES_KEPT]
        return score


def replay_trend_scores(
    symbol: str, timeframe: str, bars: Iterable[Any], warmup: int = REPLAY_WARMUP_BARS,
) -> dict[datetime, float | None]:
    """``{bar timestamp: trend score}`` for ``bars`` (oldest first, closed, each with
    ``timestamp``, ``close`` and ``volume``). The score is None for warm-up bars, for bars the
    engine could not score, and for every bar of a timeframe the engine does not support.
    """
    replay = BarReplay(symbol, timeframe, warmup)
    return {bar.timestamp: replay.feed(bar) for bar in bars}


_LABEL_FIELDS = ("trend_score", "trend_state", "strength", "momentum", "structure", "market_regime")


def _same(old: Any, new: Any) -> bool:
    if isinstance(old, float) and isinstance(new, float):
        return abs(old - new) < 1e-9
    return old == new


def relabel_signals(db, symbol: str, timeframe: str, chunk_size: int = 5000,
                    dry_run: bool = False) -> dict[str, int]:
    """Rewrite the label columns of every stored signal of one (symbol, timeframe) from a replay
    of that pair's stored bars. Forward outcomes and prices are not touched: they come from the
    bars alone and were never wrong.

    A signal with no point-in-time score -- warm-up, or no bar left to replay -- is set to an
    all-None label: its old value was one score stamped on the whole batch, and an honest
    "unknown" beats it. Idempotent. Commits every ``chunk_size`` rows, in short transactions, so
    it can run against the live database. Returns ``{"rows", "changed", "labelled", "unlabelled"}``
    (``changed`` is what would be written when ``dry_run``).
    """
    from sqlalchemy import update

    from backend.models import BarModel, HistoricalSignal

    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    sym = symbol.upper()
    bars = (
        db.query(BarModel.timestamp, BarModel.close, BarModel.volume)
        .filter(BarModel.symbol == sym, BarModel.timeframe == timeframe)
        .order_by(BarModel.timestamp.asc())
        .all()
    )
    scores = replay_trend_scores(sym, timeframe, bars)

    rows = db.query(
        HistoricalSignal.id, HistoricalSignal.timestamp, HistoricalSignal.trend_score,
        HistoricalSignal.trend_state, HistoricalSignal.strength, HistoricalSignal.momentum,
        HistoricalSignal.structure, HistoricalSignal.market_regime,
    ).filter(HistoricalSignal.symbol == sym, HistoricalSignal.timeframe == timeframe).all()

    stats = {"rows": len(rows), "changed": 0, "labelled": 0, "unlabelled": 0}
    pending: list[dict[str, Any]] = []

    def flush() -> None:
        if pending and not dry_run:
            db.execute(update(HistoricalSignal), pending)
            db.commit()
        pending.clear()

    for row in rows:
        label = label_columns(scores.get(row.timestamp))
        stats["labelled" if label["trend_score"] is not None else "unlabelled"] += 1
        if all(_same(getattr(row, f), label[f]) for f in _LABEL_FIELDS):
            continue
        stats["changed"] += 1
        pending.append({"id": row.id, **label})
        if len(pending) >= chunk_size:
            flush()
    flush()
    return stats


_BACKUP_COLUMNS = ("id", *_LABEL_FIELDS)


def export_labels(db, path) -> int:
    """Write every signal's current label columns to ``path`` (gzipped CSV) so ``relabel_signals``
    can be undone with ``restore_labels``. Returns the row count."""
    import csv
    import gzip

    from backend.models import HistoricalSignal

    n = 0
    cols = [getattr(HistoricalSignal, c) for c in _BACKUP_COLUMNS]
    with gzip.open(path, "wt", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_BACKUP_COLUMNS)
        for row in db.query(*cols).order_by(HistoricalSignal.id).yield_per(20000):
            writer.writerow(["" if v is None else v for v in row])
            n += 1
    return n


def restore_labels(db, path, chunk_size: int = 5000) -> int:
    """Put back the label columns saved by ``export_labels`` (rows that no longer exist are
    skipped). Returns the number of rows written."""
    import csv
    import gzip

    from sqlalchemy import update

    from backend.models import HistoricalSignal

    floats = {"trend_score", "strength", "momentum"}
    n = 0
    batch: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal n
        if batch:
            db.execute(update(HistoricalSignal), batch)
            db.commit()
            n += len(batch)
            batch.clear()

    with gzip.open(path, "rt", newline="") as fh:
        for rec in csv.DictReader(fh):
            row: dict[str, Any] = {"id": int(rec["id"])}
            for c in _LABEL_FIELDS:
                v = rec[c]
                row[c] = None if v == "" else (float(v) if c in floats else v)
            batch.append(row)
            if len(batch) >= chunk_size:
                flush()
    flush()
    return n
