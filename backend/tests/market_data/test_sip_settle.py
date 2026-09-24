"""
MD-03: stored 1m bars are settled from Alpaca's consolidated (SIP) feed.

Webull's extended-hours 1m volume is about half the market's, and its stream
(Nasdaq Basic) less. Once a minute is 15 minutes old, its SIP bar replaces the
stored one and no other provider may overwrite it; the 2m-4h bars over it are
rebuilt.
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from backend.database import SessionLocal
from backend.market_data.services.ingestion_service import MarketDataIngestionService
from backend.models import Bar, BarModel, DataStatus

SYMBOL = "ZZSETTLE"
# Wednesday, after hours: the minutes being settled are after-hours minutes.
NOW = datetime(2026, 9, 9, 17, 30)


def _bar(ts, volume, provider, close=100.0):
    return Bar(
        symbol=SYMBOL,
        timeframe="1m",
        open=close,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=volume,
        timestamp=ts,
        provider=provider,
        data_status=DataStatus.HISTORICAL,
    )


class _FakeAlpaca:
    def __init__(self, bars):
        self.bars = bars
        self.calls = []

    def get_bars_between(self, symbol, timeframe, start, end):
        self.calls.append((symbol, timeframe, start, end))
        return [b.model_copy() for b in self.bars]


class TestSettle1mFromSip(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = SessionLocal()
        self._clear()
        self.service = MarketDataIngestionService(symbols=[SYMBOL], timeframes=["1m"])
        self.start = NOW.replace(hour=16, minute=0)
        # Webull's after-hours minutes: about half the SIP volume.
        from backend.repositories.bar_repository import upsert_bars

        upsert_bars(
            self.db,
            [_bar(self.start + timedelta(minutes=m), 500, "webull") for m in range(90)],
        )
        self.db.commit()

    def tearDown(self):
        self._clear()
        self.db.close()

    def _clear(self):
        self.db.query(BarModel).filter(BarModel.symbol == SYMBOL).delete()
        self.db.commit()

    async def _settle(self, sip_bars):
        fake = _FakeAlpaca(sip_bars)
        with patch("backend.market_data.services.manager.get_cached_provider", return_value=fake):
            written = await self.service._settle_1m_from_sip(self.start, NOW)
        return written, fake

    def _row(self, timeframe, ts):
        self.db.expire_all()
        return (
            self.db.query(BarModel)
            .filter(
                BarModel.symbol == SYMBOL,
                BarModel.timeframe == timeframe,
                BarModel.timestamp == ts,
            )
            .one_or_none()
        )

    async def test_settled_minutes_take_the_sip_bar(self):
        sip = [_bar(self.start + timedelta(minutes=m), 1000, "alpaca", 101.0) for m in range(90)]
        written, fake = await self._settle(sip)

        # Only minutes that ended 15 minutes before NOW (17:15) are settled.
        self.assertEqual(written, 75)
        settled = self._row("1m", self.start + timedelta(minutes=10))
        self.assertEqual((settled.provider, settled.volume, settled.close), ("alpaca", 1000, 101.0))
        self.assertEqual(settled.session, "after_hours")
        recent = self._row("1m", NOW - timedelta(minutes=10))
        self.assertEqual((recent.provider, recent.volume), ("webull", 500))

        _, _, start, end = fake.calls[0]
        self.assertEqual(end.replace(tzinfo=None), NOW - timedelta(minutes=15))
        self.assertEqual(start.replace(tzinfo=None), self.start)

    async def test_bars_built_over_settled_minutes_are_rebuilt(self):
        sip = [_bar(self.start + timedelta(minutes=m), 1000, "alpaca") for m in range(90)]
        with patch("backend.market_data.services.ingestion_service.datetime") as clock:
            clock.now.side_effect = lambda tz=None: NOW.replace(tzinfo=tz) if tz else NOW
            await self._settle(sip)

        five = self._row("5m", self.start + timedelta(minutes=5))
        self.assertEqual(five.volume, 5 * 1000)
        hour = self._row("1h", self.start)
        self.assertEqual((hour.provider, hour.volume), ("live_from_1m", 60 * 1000))

    async def test_iex_bars_never_settle(self):
        """``alpaca_iex`` means SIP was refused: one exchange's volume must not replace Webull's."""
        iex = [_bar(self.start + timedelta(minutes=m), 20, "alpaca_iex") for m in range(90)]
        written, _ = await self._settle(iex)
        self.assertEqual(written, 0)
        self.assertEqual(self._row("1m", self.start).provider, "webull")

    async def test_webull_cannot_overwrite_a_settled_minute(self):
        from backend.repositories.bar_repository import upsert_bars

        await self._settle([_bar(self.start, 1000, "alpaca")])
        upsert_bars(self.db, [_bar(self.start, 500, "webull")])
        self.db.commit()
        self.assertEqual(self._row("1m", self.start).volume, 1000)

    async def test_no_alpaca_provider_is_a_no_op(self):
        with patch("backend.market_data.services.manager.get_cached_provider", return_value=None):
            self.assertEqual(await self.service._settle_1m_from_sip(self.start, NOW), 0)


if __name__ == "__main__":
    unittest.main()


class TestSettleSymbolOneOff(unittest.TestCase):
    """The one-off rewrite of stored 1m history (backend/market_data/sip_settle.py)."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        self.engine = create_engine("sqlite://")
        BarModel.__table__.create(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        # 1m from 07:37 (the 07:00 hour cut short by the prune) to 09:59, from three sources.
        start = NOW.replace(hour=7, minute=37)
        providers = {0: "webull", 1: "webull_stream", 2: "alpaca_iex"}
        self.db.add_all(
            BarModel(
                symbol=SYMBOL,
                timeframe="1m",
                open=100,
                high=101,
                low=99,
                close=100,
                volume=10,
                timestamp=start + timedelta(minutes=m),
                provider=providers[m % 3],
                data_status="HISTORICAL",
                session="premarket",
            )
            for m in range(143)
        )
        self.db.commit()

    def _fetch(self, symbol, start, end):
        self.fetched = (start, end)
        minutes = [
            start + timedelta(minutes=m) for m in range(int((end - start).total_seconds() // 60))
        ]
        return [_bar(ts, 100, "alpaca", 102.0) for ts in minutes]

    def _rows(self, timeframe):
        return {
            r.timestamp: r for r in self.db.query(BarModel).filter(BarModel.timeframe == timeframe)
        }

    def test_replaces_every_whole_hour_and_rebuilds_what_is_built_on_it(self):
        from backend.market_data.sip_settle import settle_symbol

        until = NOW.replace(hour=9, minute=45)
        report = settle_symbol(self.db, SYMBOL, self._fetch, until, NOW)

        self.assertEqual(self.fetched, (NOW.replace(hour=8, minute=0), until))
        minutes = self._rows("1m")
        self.assertEqual(minutes[NOW.replace(hour=8, minute=0)].provider, "alpaca")
        self.assertEqual(minutes[NOW.replace(hour=9, minute=40)].session, "regular")
        self.assertEqual(minutes[NOW.replace(hour=9, minute=50)].provider, "webull_stream")
        self.assertEqual(minutes[NOW.replace(hour=7, minute=40)].volume, 10)  # before the window
        self.assertEqual(sum(report.replaced.values()), 105)
        self.assertEqual(report.added, 0)

        five = self._rows("5m")[NOW.replace(hour=8, minute=0)]
        self.assertEqual((five.volume, five.close, five.session), (500, 102.0, "premarket"))
        hour = self._rows("1h")[NOW.replace(hour=8, minute=0)]
        self.assertEqual((hour.provider, hour.volume), ("live_from_1m", 6000))
        self.assertEqual(self._rows("4h")[NOW.replace(hour=8, minute=0)].volume, 6000 + 45 * 100 + 15 * 10)

    def test_iex_bars_from_a_refused_sip_request_are_ignored(self):
        from backend.market_data.sip_settle import settle_symbol

        def iex(symbol, start, end):
            return [_bar(start, 1, "alpaca_iex")]

        report = settle_symbol(self.db, SYMBOL, iex, NOW, NOW)
        self.assertEqual(sum(report.replaced.values()), 0)
        self.assertEqual(self._rows("1m")[NOW.replace(hour=8, minute=0)].provider, "alpaca_iex")  # as stored

    def test_writes_nothing_until_the_caller_commits(self):
        from backend.market_data.sip_settle import settle_symbol

        settle_symbol(self.db, SYMBOL, self._fetch, NOW, NOW)
        self.db.rollback()
        self.assertEqual(
            {r.provider for r in self._rows("1m").values()},
            {"webull", "webull_stream", "alpaca_iex"},
        )
        self.assertEqual(self._rows("5m"), {})
