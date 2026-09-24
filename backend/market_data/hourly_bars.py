"""
Build 1h and 4h bars on the clock-hour grid (MD-01).

Stored 1h bars start on the hour: the "10:00" bar covers 10:00-11:00 ET.
Webull and Yahoo return regular-session hourly bars anchored on the half
hour instead (09:30-10:30, ...). Such a bar straddles two clock hours, so no
relabelling can place it correctly; ingestion skips it. Hours come from our
own 1m bars where they exist, and otherwise from a provider whose hourly bars
start on the hour (Alpaca).

These helpers are shared by the live ingestion loops and
``scripts/repair_hourly_bars.py``. This module must not import the
market-data manager: building it authenticates every provider, Webull
included.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from backend.engines.market_calendar import aggregate_bar_session, classify_bar_session
from backend.models import Bar, DataStatus

LIVE_FROM_1M = "live_from_1m"
AGGREGATED_FROM_1H = "aggregated_from_1h"

_HOUR = timedelta(hours=1)
_FOUR_HOURS = timedelta(hours=4)


def hour_session(hour_start: datetime) -> str:
    """Session of the clock hour starting at ``hour_start`` (naive NY).

    Session edges (04:00, 09:30, 16:00, 20:00) fall on the hour or the half
    hour, so the two halves decide it: the 09:00 hour is "mixed".
    """
    return aggregate_bar_session(
        classify_bar_session(hour_start + half) for half in (timedelta(0), timedelta(minutes=30))
    )


def build_1h_from_1m(
    symbol: str, hour_start: datetime, members: list[Bar], now: datetime
) -> Bar | None:
    """The 1h bar for ``hour_start`` from that hour's 1m bars (oldest first).

    ``None`` when fewer than two 1m bars exist, too little of the hour to
    stand for it. The bar is INCOMPLETE until its hour has ended at ``now``
    (naive NY).
    """
    if len(members) < 2:
        return None
    hour_end = hour_start + _HOUR
    return Bar(
        symbol=symbol.upper(),
        timeframe="1h",
        open=members[0].open,
        high=max(b.high for b in members),
        low=min(b.low for b in members),
        close=members[-1].close,
        volume=sum(b.volume for b in members),
        timestamp=hour_start,
        provider=LIVE_FROM_1M,
        data_status=DataStatus.INCOMPLETE if hour_end > now else DataStatus.HISTORICAL,
        # The 09:00 hour spans premarket and regular; use the members' own sessions.
        session=aggregate_bar_session(b.session for b in members),
    )


def aggregate_1h_to_4h(symbol: str, hourly: Iterable[Bar], now: datetime) -> list[Bar]:
    """4h bars (buckets from 00:00, 04:00, 08:00, ... ET) from 1h bars, oldest first.

    A bucket needs at least two 1h members, except the 16:00 bucket: before
    2026-09-17 stored 1h was regular-session only, so that bucket held only
    the 16:00 bar. The newest bucket is INCOMPLETE until it has ended.
    """
    buckets: dict[datetime, list[Bar]] = {}
    for bar in hourly:
        start = bar.timestamp.replace(
            hour=(bar.timestamp.hour // 4) * 4, minute=0, second=0, microsecond=0
        )
        buckets.setdefault(start, []).append(bar)

    out: list[Bar] = []
    for start, members in sorted(buckets.items()):
        if len(members) < (1 if start.hour == 16 else 2):
            continue
        out.append(
            Bar(
                symbol=symbol.upper(),
                timeframe="4h",
                open=members[0].open,
                high=max(b.high for b in members),
                low=min(b.low for b in members),
                close=members[-1].close,
                volume=sum(b.volume for b in members),
                timestamp=start,
                provider=AGGREGATED_FROM_1H,
                data_status=(
                    DataStatus.HISTORICAL if start + _FOUR_HOURS < now else DataStatus.INCOMPLETE
                ),
                # The 08:00 bucket spans premarket and regular; use the members' own sessions.
                session=aggregate_bar_session(b.session for b in members),
            )
        )
    return out


def closed_provider_hours(bars: Iterable[Bar], available_until: datetime) -> list[Bar]:
    """Provider 1h bars whose hour had ended by ``available_until`` (naive NY).

    Alpaca's free plan serves consolidated (SIP) data only up to 15 minutes
    ago, so the newest hourly bar it returns can be cut short; storing it as
    HISTORICAL would freeze a partial hour.
    """
    return [b for b in bars if b.timestamp + _HOUR <= available_until]
