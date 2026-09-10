"""
Tests for ChatRepository — Version 4, AI feature 4.

Fresh in-memory SQLite per test (same convention as
test_bar_repository.py / test_signal_repository.py) so chat rows
never touch the real marketlens.db.
"""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import ChatMessage, ChatSession
from backend.repositories.chat_repository import ChatRepository


class TestChatRepository(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False},
        )
        for model in (ChatSession, ChatMessage):
            model.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _repo(self):
        return ChatRepository(self.Session())

    def test_create_session(self):
        repo = self._repo()
        session = repo.create_session("aapl")
        self.assertIsNotNone(session.id)
        self.assertEqual(session.symbol, "AAPL")
        self.assertIsNone(session.alert_trigger_id)

    def test_create_session_with_alert_trigger(self):
        repo = self._repo()
        session = repo.create_session("AAPL", alert_trigger_id=42)
        self.assertEqual(session.alert_trigger_id, 42)

    def test_get_or_create_open_session_creates_when_none_exists(self):
        repo = self._repo()
        session = repo.get_or_create_open_session("AAPL")
        self.assertIsNotNone(session.id)
        self.assertEqual(session.symbol, "AAPL")

    def test_get_or_create_open_session_reuses_existing(self):
        repo = self._repo()
        first = repo.get_or_create_open_session("AAPL")
        second = repo.get_or_create_open_session("AAPL")
        self.assertEqual(first.id, second.id)

    def test_get_or_create_open_session_new_alert_trigger_creates_new_session(self):
        """A chat opened from a specific alert trigger should see that
        trigger's context from the first message, not silently reuse
        an unrelated open session for the same symbol."""
        repo = self._repo()
        plain = repo.get_or_create_open_session("AAPL")
        scoped = repo.get_or_create_open_session("AAPL", alert_trigger_id=7)
        self.assertNotEqual(plain.id, scoped.id)
        self.assertEqual(scoped.alert_trigger_id, 7)

    def test_get_or_create_open_session_reuses_same_alert_trigger(self):
        repo = self._repo()
        first = repo.get_or_create_open_session("AAPL", alert_trigger_id=7)
        second = repo.get_or_create_open_session("AAPL", alert_trigger_id=7)
        self.assertEqual(first.id, second.id)

    def test_get_session_found_and_not_found(self):
        repo = self._repo()
        session = repo.create_session("AAPL")
        self.assertEqual(repo.get_session(session.id).id, session.id)
        self.assertIsNone(repo.get_session(999999))

    def test_add_message_and_get_messages_ordering(self):
        repo = self._repo()
        session = repo.create_session("AAPL")
        repo.add_message(session.id, "user", "first")
        repo.add_message(session.id, "assistant", "second")
        repo.add_message(session.id, "user", "third")

        messages = repo.get_messages(session.id)
        self.assertEqual([m.content for m in messages], ["first", "second", "third"])
        self.assertEqual([m.role for m in messages], ["user", "assistant", "user"])

    def test_add_message_updates_session_updated_at(self):
        repo = self._repo()
        session = repo.create_session("AAPL")
        original_updated = session.updated_at
        repo.add_message(session.id, "user", "hi")
        refreshed = repo.get_session(session.id)
        self.assertIsNotNone(refreshed.updated_at)

    def test_get_messages_respects_limit_keeping_most_recent(self):
        repo = self._repo()
        session = repo.create_session("AAPL")
        for i in range(5):
            repo.add_message(session.id, "user", f"msg{i}")

        messages = repo.get_messages(session.id, limit=2)
        self.assertEqual([m.content for m in messages], ["msg3", "msg4"])

    def test_get_messages_empty_session(self):
        repo = self._repo()
        session = repo.create_session("AAPL")
        self.assertEqual(repo.get_messages(session.id), [])


if __name__ == "__main__":
    unittest.main()
