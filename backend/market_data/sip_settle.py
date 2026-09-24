"""
Settle stored 1m bars from Alpaca's consolidated (SIP) feed, in one go (MD-03).

The live ingestion loop settles each minute 15 minutes after it closes
(``MarketDataIngestionService._settle_1m_from_sip``). ``settle_symbol`` does
the same for a whole stored window, for the one-off rewrite of the 1m history
written before that loop existed: Webull's extended-hours minutes (about half
the market's volume), Webull stream minutes (Nasdaq Basic), and Alpaca IEX
minutes (a few percent). It then rebuilds the 2m-30m, 1h and 4h bars over the
window. Historical signals are not touched: they keep what the engine saw
live. Nothing is committed here: the caller commits or rolls back.

Run it through ``scripts/settle_1m_from_sip.py``. Like ``hourly_repair`` this
module must not import the market-data manager, which authenticates Webull.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.engines.market_calendar import classify_bar_session
from backend.market_data.hourly_bars import aggregate_1h_to_4h, build_1h_from_1m
from backend.market_data.hourly_repair import HourlyFetch, write_bars
from backend.models import Bar, BarModel, DataStatus
from backend.utils.resampler import resample_ohlcv

SUBHOUR_TIMEFRAMES = {"2m": 2, "3m": 3, "5m": 5, "15m": 15, "30m": 30}
SIP_PROVIDER = "alpaca"
_HOUR = timedelta(hours=1)


@dataclass
class SymbolSettle:
    symbol: str
    replaced: Counter = field(default_factory=Counter)  # stored provider -> minutes replaced
    added: int = 0  # SIP minutes that had no stored bar
    volume_before: int = 0
    volume_after: int = 0
    rebuilt: Counter = field(default_factory=Counter)  # timeframe -> bars written


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


def _minutes(db: Session, symbol: str, since: datetime) -> list[BarModel]:
    return (
        db.query(BarModel)
        .filter(BarModel.symbol == symbol, BarModel.timeframe == "1m", BarModel.timestamp >= since)
        .order_by(BarModel.timestamp.asc())
        .all()
    )


def settle_symbol(
    db: Session, symbol: str, fetch: HourlyFetch, until: datetime, now: datetime
) -> SymbolSettle:
    """Replace ``symbol``'s stored 1m bars before ``until`` with SIP bars and rebuild the
    timeframes built from them. Starts at the symbol's oldest whole stored hour of 1m bars.
    ``until`` and ``now`` are naive NY."""
    symbol = symbol.upper()
    report = SymbolSettle(symbol)
    first = (
        db.query(func.min(BarModel.timestamp))
        .filter(BarModel.symbol == symbol, BarModel.timeframe == "1m")
        .scalar()
    )
    if first is None:
        return report
    # The oldest 1m hour is usually cut short by the retention prune; start at the next one.
    since = first.replace(minute=0, second=0, microsecond=0)
    if since < first:
        since += _HOUR

    sip = [
        b
        for b in fetch(symbol, since, until)
        if b.provider == SIP_PROVIDER and since <= b.timestamp < until
    ]
    stored = {r.timestamp: r for r in _minutes(db, symbol, since) if r.timestamp < until}
    report.volume_before = sum(r.volume for r in stored.values())
    for b in sip:
        b.symbol = symbol
        b.timeframe = "1m"
        b.data_status = DataStatus.HISTORICAL
        b.session = classify_bar_session(b.timestamp)
        row = stored.get(b.timestamp)
        if row is None:
            report.added += 1
        elif row.provider != SIP_PROVIDER:
            report.replaced[row.provider] += 1
    write_bars(db, sip)

    minutes = [_to_bar(r) for r in _minutes(db, symbol, since)]
    report.volume_after = sum(b.volume for b in minutes if b.timestamp < until)

    for timeframe, size in SUBHOUR_TIMEFRAMES.items():
        bars = resample_ohlcv(minutes, timeframe)
        for bar in bars:
            bar.provider = "aggregated_from_1m"
            ended = bar.timestamp + timedelta(minutes=size) < now
            bar.data_status = DataStatus.HISTORICAL if ended else DataStatus.INCOMPLETE
            bar.session = classify_bar_session(bar.timestamp)
        write_bars(db, bars)
        report.rebuilt[timeframe] = len(bars)

    by_hour: dict[datetime, list[Bar]] = {}
    for bar in minutes:
        by_hour.setdefault(bar.timestamp.replace(minute=0, second=0, microsecond=0), []).append(bar)
    hourly = [
        bar
        for hour, members in sorted(by_hour.items())
        if (bar := build_1h_from_1m(symbol, hour, members, now)) is not None
    ]
    write_bars(db, hourly)
    report.rebuilt["1h"] = len(hourly)

    four_start = since.replace(hour=(since.hour // 4) * 4)
    source = (
        db.query(BarModel)
        .filter(
            BarModel.symbol == symbol,
            BarModel.timeframe == "1h",
            BarModel.timestamp >= four_start,
        )
        .order_by(BarModel.timestamp.asc())
        .all()
    )
    four_hour = aggregate_1h_to_4h(symbol, (_to_bar(r) for r in source), now)
    write_bars(db, four_hour)
    report.rebuilt["4h"] = len(four_hour)
    return report
