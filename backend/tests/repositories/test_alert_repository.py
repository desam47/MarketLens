"""
Tests for AlertRepository — focused on delete_all_triggers() (the
"Clear history" backend, 2026-09-11) and the cascade-delete behavior
it exists alongside. Fresh in-memory SQLite per test, same convention
as test_chat_repository.py / test_bar_repository.py.
"""

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Alert, AlertTrigger
from backend.repositories.alert_repository import AlertRepository


class TestAlertRepository(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        for model in (Alert, AlertTrigger):
            model.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _repo(self) -> AlertRepository:
        return AlertRepository(self.db)

    def test_delete_cascades_to_its_own_triggers(self):
        repo = self._repo()
        alert = repo.create("A", "AAPL", "price_above", "200")
        self.db.add(AlertTrigger(alert_id=alert.id, symbol="AAPL", message="hi"))
        self.db.commit()

        repo.delete(alert.id)

        self.assertEqual(self.db.query(AlertTrigger).count(), 0)

    def test_delete_all_triggers_clears_everything(self):
        repo = self._repo()
        a1 = repo.create("A", "AAPL", "price_above", "200")
        a2 = repo.create("B", "MSFT", "price_below", "300")
        self.db.add_all(
            [
                AlertTrigger(alert_id=a1.id, symbol="AAPL", message="one"),
                AlertTrigger(alert_id=a1.id, symbol="AAPL", message="two"),
                AlertTrigger(alert_id=a2.id, symbol="MSFT", message="three"),
            ]
        )
        self.db.commit()

        deleted = repo.delete_all_triggers()

        self.assertEqual(deleted, 3)
        self.assertEqual(self.db.query(AlertTrigger).count(), 0)
        # the alerts themselves are untouched — this only clears history
        self.assertEqual(self.db.query(Alert).count(), 2)

    def test_delete_all_triggers_sweeps_up_orphaned_rows(self):
        # A trigger whose parent alert row is already gone (e.g. from
        # before the cascade-delete relationship existed) — the exact
        # shape of the 3 rows found live 2026-09-11.
        repo = self._repo()
        self.db.add(AlertTrigger(alert_id=999, symbol="AAPL", message="orphaned"))
        self.db.commit()

        deleted = repo.delete_all_triggers()

        self.assertEqual(deleted, 1)
        self.assertEqual(self.db.query(AlertTrigger).count(), 0)

    def test_delete_all_triggers_on_empty_table_is_a_clean_noop(self):
        self.assertEqual(self._repo().delete_all_triggers(), 0)


if __name__ == "__main__":
    unittest.main()
