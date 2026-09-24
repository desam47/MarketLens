"""
Repair stored 1h and 4h bars that were filed 30 minutes early (MD-01).

Until 2026-09-24 Webull and Yahoo hourly bars, which start on the half hour
(09:30-10:30), were floored to the hour and stored as "09:00". Alpaca IEX
bars filled a few other hours with a few percent of the market's volume
(MD-03). ``repair_symbol`` rebuilds one symbol's 1h series on the clock-hour
grid:

1. hours covered by our 1m bars are built from them;
2. 1h bars already built from 1m (``live_from_1m``) are kept: they are exact;
3. every other hour comes from Alpaca's consolidated (SIP) hourly bars;
4. every other stored provider 1h bar is deleted, replaced or not.

It then rebuilds the symbol's 4h bars from the repaired 1h series and deletes
its 1h and 4h historical signals, which were scored on the shifted bars; the
signal recorder re-records them from the repaired bars when the server next
starts. Nothing is committed here: the caller commits or rolls back.

Run it through ``scripts/repair_hourly_bars.py``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.market_data.hourly_bars import (
    LIVE_FROM_1M,
    aggregate_1h_to_4h,
    build_1h_from_1m,
    closed_provider_hours,
    hour_session,
)
from backend.models import Bar, BarModel, DataStatus, HistoricalSignal

# (symbol, start, end) -> hourly bars, timestamps naive NY. ``start``/``end`` are naive NY.
HourlyFetch = Callable[[str, datetime, datetime], list[Bar]]

_HOUR = timedelta(hours=1)
_UPDATED_COLUMNS = ("open", "high", "low", "close", "volume", "provider", "data_status", "session")
# Alpaca's free plan serves SIP data only up to 15 minutes ago.
_SIP_DELAY = timedelta(minutes=15)


@dataclass
class SymbolRepair:
    symbol: str
    before_1h: Counter = field(default_factory=Counter)
    after_1h: Counter = field(default_factory=Counter)
    built_from_1m: int = 0
    from_provider: int = 0
    dropped: int = 0
    before_4h: int = 0
    after_4h: int = 0
    signals_deleted: Counter = field(default_factory=Counter)
    first_hour: datetime | None = None


def _to_bar(row: BarModel) -> Bar:
    return Bar(
        symbol=row.symbol,
        timeframe=row.timeframe,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        timestamp=row.timestamp,
        provider=row.provider,
        data_status=DataStatus(row.data_status),
        session=row.session,
    )


def write_bars(db: Session, bars: list[Bar]) -> None:
    """Upsert ``bars`` in the caller's transaction. ``upsert_bars`` commits, and a dry run must
    be able to roll everything back."""
    if not bars:
        return
    stmt = sqlite_insert(BarModel)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "timeframe", "timestamp"],
        set_={col: stmt.excluded[col] for col in _UPDATED_COLUMNS},
    )
    db.connection().execute(
        stmt,
        [
            {
                "symbol": b.symbol.upper(),
                "timeframe": b.timeframe,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "timestamp": b.timestamp,
                "provider": b.provider,
                "data_status": DataStatus(b.data_status).value,
                "source": "raw",
                "session": b.session,
            }
            for b in bars
        ],
    )
    # The rows were written behind the ORM's back; don't serve stale copies of them.
    db.expire_all()


def _provider_counts(db: Session, symbol: str) -> Counter:
    rows = (
        db.query(BarModel.provider, func.count())
        .filter(BarModel.symbol == symbol, BarModel.timeframe == "1h")
        .group_by(BarModel.provider)
        .all()
    )
    return Counter(dict(rows))


def _hours_from_1m(db: Session, symbol: str, now: datetime) -> dict[datetime, Bar]:
    """1h bars built from the symbol's 1m bars, for every whole hour the 1m data covers.

    The oldest 1m hour is usually cut short by the retention prune, so hours before the first
    1m bar's hour boundary are left out."""
    rows = (
        db.query(BarModel)
        .filter(BarModel.symbol == symbol, BarModel.timeframe == "1m")
        .order_by(BarModel.timestamp.asc())
        .all()
    )
    if not rows:
        return {}
    first = rows[0].timestamp
    first_whole_hour = first.replace(minute=0, second=0, microsecond=0)
    if first_whole_hour < first:
        first_whole_hour += _HOUR

    by_hour: dict[datetime, list[Bar]] = {}
    for row in rows:
        hour = row.timestamp.replace(minute=0, second=0, microsecond=0)
        if hour >= first_whole_hour:
            by_hour.setdefault(hour, []).append(_to_bar(row))
    built = {}
    for hour, members in by_hour.items():
        bar = build_1h_from_1m(symbol, hour, members, now)
        if bar is not None:
            built[hour] = bar
    return built


def _provider_hours(fetch: HourlyFetch, symbol: str, start: datetime, now: datetime) -> list[Bar]:
    """Closed on-the-hour provider bars from ``start`` to now, sessions set from the hour."""
    bars = [
        b
        for b in fetch(symbol, start, now)
        if b.timestamp >= start and b.timestamp.minute == 0 and b.timestamp.second == 0
    ]
    for b in bars:
        b.symbol = symbol
        b.timeframe = "1h"
        b.data_status = DataStatus.HISTORICAL
        b.session = hour_session(b.timestamp)
    return closed_provider_hours(bars, now - _SIP_DELAY)


def repair_symbol(db: Session, symbol: str, fetch: HourlyFetch, now: datetime) -> SymbolRepair:
    """Rebuild ``symbol``'s 1h and 4h bars and drop its 1h/4h signals. ``now`` is naive NY."""
    symbol = symbol.upper()
    report = SymbolRepair(symbol)
    report.before_1h = _provider_counts(db, symbol)
    report.first_hour = (
        db.query(func.min(BarModel.timestamp))
        .filter(BarModel.symbol == symbol, BarModel.timeframe == "1h")
        .scalar()
    )
    if report.first_hour is None:
        return report
    start = report.first_hour.replace(hour=0, minute=0, second=0, microsecond=0)

    # Fetch before deleting anything, so a provider failure leaves the symbol untouched.
    built = _hours_from_1m(db, symbol, now)
    provider_bars = _provider_hours(fetch, symbol, start, now)

    provider_hours = {
        ts
        for (ts,) in db.query(BarModel.timestamp).filter(
            BarModel.symbol == symbol, BarModel.timeframe == "1h", BarModel.provider != LIVE_FROM_1M
        )
    }
    db.query(BarModel).filter(
        BarModel.symbol == symbol, BarModel.timeframe == "1h", BarModel.provider != LIVE_FROM_1M
    ).delete(synchronize_session=False)
    kept = {
        ts
        for (ts,) in db.query(BarModel.timestamp).filter(
            BarModel.symbol == symbol, BarModel.timeframe == "1h"
        )
    }

    write_bars(db, list(built.values()))
    fill = [b for b in provider_bars if b.timestamp not in built and b.timestamp not in kept]
    write_bars(db, fill)
    report.built_from_1m = len(built)
    report.from_provider = len(fill)

    report.after_1h = _provider_counts(db, symbol)
    report.dropped = len(provider_hours - set(built) - {b.timestamp for b in fill})

    report.before_4h = (
        db.query(BarModel)
        .filter(BarModel.symbol == symbol, BarModel.timeframe == "4h")
        .delete(synchronize_session=False)
    )
    hourly = (
        db.query(BarModel)
        .filter(BarModel.symbol == symbol, BarModel.timeframe == "1h")
        .order_by(BarModel.timestamp.asc())
        .all()
    )
    four_hour = aggregate_1h_to_4h(symbol, (_to_bar(r) for r in hourly), now)
    write_bars(db, four_hour)
    report.after_4h = len(four_hour)

    for timeframe in ("1h", "4h"):
        report.signals_deleted[timeframe] = (
            db.query(HistoricalSignal)
            .filter(HistoricalSignal.symbol == symbol, HistoricalSignal.timeframe == timeframe)
            .delete(synchronize_session=False)
        )
    return report


def misplaced_hours(db: Session, symbol: str) -> int:
    """1h bars for ``symbol`` whose stored close differs from their hour's last 1m close.

    Only hours with at least two 1m bars are checked. After a repair this is 0."""
    hourly = {
        r.timestamp: r.close
        for r in db.query(BarModel.timestamp, BarModel.close).filter(
            BarModel.symbol == symbol, BarModel.timeframe == "1h"
        )
    }
    last_close: dict[datetime, tuple[datetime, float]] = {}
    count: Counter = Counter()
    first_1m: datetime | None = None
    for ts, close in db.query(BarModel.timestamp, BarModel.close).filter(
        BarModel.symbol == symbol, BarModel.timeframe == "1m"
    ):
        hour = ts.replace(minute=0, second=0, microsecond=0)
        count[hour] += 1
        if hour not in last_close or ts > last_close[hour][0]:
            last_close[hour] = (ts, close)
        first_1m = ts if first_1m is None else min(first_1m, ts)
    if first_1m is None:
        return 0
    # The oldest 1m hour is usually cut short by the retention prune; skip it.
    first_whole_hour = first_1m.replace(minute=0, second=0, microsecond=0)
    if first_whole_hour < first_1m:
        first_whole_hour += _HOUR
    return sum(
        1
        for hour, (_, close) in last_close.items()
        if hour >= first_whole_hour
        and count[hour] >= 2
        and hour in hourly
        and abs(hourly[hour] - close) > 1e-9
    )
