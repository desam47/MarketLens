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
from datetime import datetime
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


def replay_trend_scores(
    symbol: str, timeframe: str, bars: Iterable[Any], warmup: int = REPLAY_WARMUP_BARS,
) -> dict[datetime, float | None]:
    """``{bar timestamp: trend score}`` for ``bars`` (oldest first, each with ``timestamp``,
    ``close`` and ``volume``). The score is None for warm-up bars, for bars the engine could not
    score, and for every bar of a timeframe the engine does not support.
    """
    from backend.engines.timeframe import Timeframe, TimeframeEngine, multi_symbol_timeframe_engine
    from backend.trend.trend_engine import TrendEngine
    from backend.utils.timezone import ny_to_utc

    bars = list(bars)
    try:
        tf = Timeframe(timeframe)
    except ValueError:
        return {b.timestamp: None for b in bars}

    # TrendEngine attaches to the PROCESS-WIDE per-symbol candle aggregator, and resets its
    # candle bookkeeping. Replaying under the real symbol would feed years of old ticks into
    # the live engine's candles. So run under a throwaway name whose aggregator is created here
    # (empty: TrendEngine would otherwise seed a new one with a price-0 tick at "now") and
    # dropped afterwards.
    scratch = f"__replay__{symbol}_{timeframe}_{uuid4().hex[:8]}"
    multi_symbol_timeframe_engine.engines[scratch] = TimeframeEngine(scratch)
    scores: dict[datetime, float | None] = {}
    try:
        engine = TrendEngine(scratch)
        for i, bar in enumerate(bars):
            score = None
            try:
                engine.update(
                    price=float(bar.close or 0.0), volume=int(bar.volume or 0),
                    timestamp=bar.timestamp, only_timeframe=tf,
                )
                signal = engine.get_current_trend(tf)
                # ``get_current_trend`` is the LAST signal ever emitted: unless it is this
                # bar's, the engine produced nothing for it and the score belongs to an
                # earlier bar.
                if (signal is not None and signal.score is not None and i >= warmup
                        and signal.timestamp == ny_to_utc(bar.timestamp)):
                    score = float(signal.score)
            except Exception:  # noqa: BLE001 - one unscorable bar must not sink the replay
                logger.debug("replay %s/%s: bar %s unscorable", symbol, timeframe,
                             bar.timestamp, exc_info=True)
            scores[bar.timestamp] = score
    finally:
        multi_symbol_timeframe_engine.engines.pop(scratch, None)
    return scores


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
