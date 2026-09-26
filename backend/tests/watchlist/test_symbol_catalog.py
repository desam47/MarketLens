"""Coverage for the shared local symbol catalog."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.models  # noqa: F401 - register every table before create_all
from backend.api.watchlist.router import get_known_symbol_catalog
from backend.database import Base
from backend.models.watchlist import Watchlist, WatchlistSymbol


class TestSymbolCatalog:
    def setup_method(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def teardown_method(self):
        self.db.close()
        self.engine.dispose()

    def test_includes_every_watchlist_symbol_and_excludes_unrelated_symbols(self):
        self.db.add_all([Watchlist(name="First"), Watchlist(name="Second")])
        self.db.flush()
        self.db.add_all(
            [
                WatchlistSymbol(watchlist_id=1, symbol="ctnt"),
                WatchlistSymbol(watchlist_id=1, symbol="CYN"),
                WatchlistSymbol(watchlist_id=2, symbol="ctnt"),
                WatchlistSymbol(watchlist_id=2, symbol="SOFI", is_enabled=False),
            ]
        )
        self.db.commit()

        symbols = get_known_symbol_catalog(db=self.db)

        assert symbols == ["CTNT", "CYN", "SOFI"]
