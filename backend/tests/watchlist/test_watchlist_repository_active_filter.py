"""
MD-04: ``symbol_exists_in_any_watchlist`` and ``all_watchlisted_symbols`` must agree with
``MarketDataIngestionService``'s own definition of "watched" — enabled in a watchlist whose
``is_active`` is true. Before this fix, a symbol left only in a DEACTIVATED (not deleted)
watchlist still counted as "watched" by the purge check, even though ingestion had already
stopped tracking it, so it was never eligible for purge.
"""

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.watchlist import Watchlist, WatchlistSymbol
from backend.repositories.watchlist_repository import WatchlistRepository


class TestActiveWatchlistFiltering(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Watchlist.__table__.create(self.engine)
        WatchlistSymbol.__table__.create(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.repo = WatchlistRepository(self.db)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)

    def _watchlist(self, name: str, is_active: bool) -> Watchlist:
        wl = Watchlist(name=name, is_active=is_active)
        self.db.add(wl)
        self.db.flush()
        return wl

    def _symbol(self, wl_id: int, symbol: str, is_enabled: bool = True) -> None:
        self.db.add(WatchlistSymbol(watchlist_id=wl_id, symbol=symbol, is_enabled=is_enabled))

    def test_symbol_in_an_active_watchlist_exists(self):
        wl = self._watchlist("Active", is_active=True)
        self._symbol(wl.id, "AAPL")
        self.db.commit()
        self.assertTrue(self.repo.symbol_exists_in_any_watchlist("aapl"))
        self.assertEqual(self.repo.all_watchlisted_symbols(), {"AAPL"})

    def test_symbol_only_in_a_deactivated_watchlist_does_not_exist(self):
        """The MD-04 gap: deactivating (not deleting) a watchlist must make its
        symbols eligible for purge, matching that ingestion has already stopped
        tracking them."""
        wl = self._watchlist("Paused", is_active=False)
        self._symbol(wl.id, "NOK")
        self.db.commit()
        self.assertFalse(self.repo.symbol_exists_in_any_watchlist("NOK"))
        self.assertEqual(self.repo.all_watchlisted_symbols(), set())

    def test_symbol_in_one_active_and_one_deactivated_watchlist_exists(self):
        active = self._watchlist("Active", is_active=True)
        paused = self._watchlist("Paused", is_active=False)
        self._symbol(active.id, "SPY")
        self._symbol(paused.id, "SPY")
        self.db.commit()
        self.assertTrue(self.repo.symbol_exists_in_any_watchlist("SPY"))

    def test_disabled_symbol_row_in_an_active_watchlist_does_not_exist(self):
        wl = self._watchlist("Active", is_active=True)
        self._symbol(wl.id, "TSLA", is_enabled=False)
        self.db.commit()
        self.assertFalse(self.repo.symbol_exists_in_any_watchlist("TSLA"))
        self.assertEqual(self.repo.all_watchlisted_symbols(), set())

    def test_unknown_symbol_does_not_exist(self):
        self.assertFalse(self.repo.symbol_exists_in_any_watchlist("ZZZZ"))


if __name__ == "__main__":
    unittest.main()
