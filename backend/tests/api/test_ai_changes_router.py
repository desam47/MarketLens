"""Contract tests for the read-only What-changed Inbox."""

import unittest
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.dependencies import get_db
from backend.api.main import app
from backend.database import Base
from backend.models.alert import Alert, AlertTrigger
from backend.models.market_data_sql import ProviderEventModel, ProviderStatusModel
from backend.models.signal import HistoricalSignal
from backend.models.watchlist import Watchlist, WatchlistSymbol


class TestAIChangesRouter(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            self.engine,
            tables=[
                Watchlist.__table__,
                WatchlistSymbol.__table__,
                Alert.__table__,
                AlertTrigger.__table__,
                HistoricalSignal.__table__,
                ProviderStatusModel.__table__,
                ProviderEventModel.__table__,
            ],
        )
        self.Session = sessionmaker(bind=self.engine)

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        self.override_db = override_db
        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        app.dependency_overrides.pop(get_db, None)
        self.engine.dispose()

    def test_returns_deduplicated_changes_and_source_links(self):
        def t(minute: int) -> datetime:
            return datetime(2026, 9, 23, 9, minute)

        db = self.Session()
        try:
            watchlist = Watchlist(name="Swing", created_at=t(1), updated_at=t(1))
            db.add(watchlist)
            db.flush()
            db.add(WatchlistSymbol(watchlist_id=watchlist.id, symbol="AAPL", added_at=t(2)))
            alert = Alert(
                name="Breakout",
                symbol="AAPL",
                condition_type="price_above",
                parameter="200",
                created_at=t(3),
                updated_at=t(3),
            )
            db.add(alert)
            db.flush()
            db.add(AlertTrigger(alert_id=alert.id, symbol="AAPL", triggered_at=t(4), message="Above 200"))
            for minute in (5, 6):
                db.add(
                    HistoricalSignal(
                        symbol="AAPL",
                        timestamp=t(minute),
                        timeframe="1h",
                        trend_state="bullish",
                        trend_score=80,
                        created_at=t(minute),
                    )
                )
            db.add(ProviderStatusModel(provider_name="webull", is_healthy=True, timestamp=t(7)))
            db.add(ProviderEventModel(provider="finnhub", method="get_news", outcome="success", timestamp=t(8)))
            db.add(ProviderEventModel(provider="webull", method="get_quote", outcome="failure", timestamp=t(9), error="timeout"))
            db.commit()
        finally:
            db.close()

        response = self.client.get(
            "/api/ai/changes?since=2026-09-23T08:00:00-04:00&limit=50"
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        categories = {item["category"] for item in body["items"]}
        self.assertTrue({"watchlist", "alert", "signal", "provider", "catalyst"}.issubset(categories))
        signal_items = [item for item in body["items"] if item["category"] == "signal"]
        self.assertEqual(len(signal_items), 1)
        self.assertTrue(all(item["href"] for item in body["items"]))
        self.assertIn("Catalyst items reflect", body["warnings"][0])

    def test_future_checkpoint_returns_empty_feed(self):
        response = self.client.get("/api/ai/changes?since=2030-01-01T00:00:00-05:00")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])


if __name__ == "__main__":
    unittest.main()
