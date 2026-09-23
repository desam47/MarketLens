"""
Tests for ChatRepository — Version 4, AI feature 4.

Fresh in-memory SQLite per test (same convention as
test_bar_repository.py / test_signal_repository.py) so chat rows
never touch the real marketlens.db.
"""

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import (
    ChatFeedback,
    ChatMessage,
    ChatRegressionFixture,
    ChatSession,
    ResearchNotebook,
    ResearchNotebookItem,
)
from backend.repositories.chat_repository import ChatRepository


class TestChatRepository(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        for model in (ChatSession, ChatMessage, ChatFeedback, ChatRegressionFixture, ResearchNotebook, ResearchNotebookItem):
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

    # --- scope (Universal AI Hub chat, 2026-09-10) --------------------

    def test_create_session_derives_scope(self):
        repo = self._repo()
        self.assertEqual(repo.create_session("AAPL").scope, "symbol")
        self.assertEqual(repo.create_session("AAPL", alert_trigger_id=1).scope, "alert")
        self.assertEqual(repo.create_session().scope, "universal")

    def test_create_universal_session_stores_sentinel_symbol(self):
        repo = self._repo()
        s = repo.create_session()
        self.assertEqual(s.scope, "universal")
        self.assertEqual(s.symbol, "*")  # UNIVERSAL_SYMBOL — column stays NOT NULL

    def test_get_or_create_universal_creates_then_reuses(self):
        repo = self._repo()
        first = repo.get_or_create_open_session()
        second = repo.get_or_create_open_session()
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.scope, "universal")

    def test_universal_and_symbol_sessions_are_disjoint(self):
        """A symbol lookup must never return the universal thread, and
        vice versa — the scope filter guarantees it."""
        repo = self._repo()
        universal = repo.get_or_create_open_session()
        aapl = repo.get_or_create_open_session("AAPL")
        self.assertNotEqual(universal.id, aapl.id)
        self.assertEqual(repo.get_or_create_open_session().id, universal.id)
        self.assertEqual(repo.get_or_create_open_session("AAPL").id, aapl.id)

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

    def test_delete_sessions_by_scope_removes_sessions_and_messages(self):
        repo = self._repo()
        u1 = repo.create_session(scope="universal")
        u2 = repo.create_session(scope="universal")
        s1 = repo.create_session("AAPL")  # scope="symbol"
        for sid in (u1.id, u2.id, s1.id):
            repo.add_message(sid, "user", "hi")
            repo.add_message(sid, "assistant", "hello")

        sessions, messages = repo.delete_sessions(scope="universal")
        self.assertEqual((sessions, messages), (2, 4))

        db = repo.db
        self.assertEqual(db.query(ChatSession).count(), 1)  # only the symbol one
        self.assertEqual(db.query(ChatSession).first().id, s1.id)
        self.assertEqual(db.query(ChatMessage).count(), 2)  # symbol session's messages kept

    def test_delete_sessions_no_filter_wipes_everything(self):
        repo = self._repo()
        repo.add_message(repo.create_session(scope="universal").id, "user", "a")
        repo.add_message(repo.create_session("MSFT").id, "user", "b")

        sessions, messages = repo.delete_sessions()
        self.assertEqual((sessions, messages), (2, 2))
        self.assertEqual(repo.db.query(ChatSession).count(), 0)
        self.assertEqual(repo.db.query(ChatMessage).count(), 0)

    def test_delete_sessions_nothing_matches(self):
        repo = self._repo()
        repo.create_session("AAPL")
        self.assertEqual(repo.delete_sessions(scope="universal"), (0, 0))
        self.assertEqual(repo.db.query(ChatSession).count(), 1)

    def test_delete_sessions_by_alert_trigger(self):
        repo = self._repo()
        a = repo.create_session("AAPL", alert_trigger_id=7)
        repo.add_message(a.id, "user", "explain this alert")
        repo.create_session("AAPL", alert_trigger_id=9)

        sessions, messages = repo.delete_sessions(alert_trigger_id=7)
        self.assertEqual((sessions, messages), (1, 1))
        self.assertEqual(repo.db.query(ChatSession).count(), 1)


class TestChatFeedback(unittest.TestCase):
    """5.7.8 feedback and correction loop."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        for model in (ChatSession, ChatMessage, ChatFeedback, ChatRegressionFixture, ResearchNotebook, ResearchNotebookItem):
            model.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _repo(self):
        return ChatRepository(self.Session())

    def test_set_feedback_creates_a_row(self):
        repo = self._repo()
        session = repo.create_session("AAPL")
        message = repo.add_message(session.id, "assistant", "AAPL is bullish.")

        feedback = repo.set_feedback(message.id, "incorrect", "wrong_data", "The price is stale.")
        self.assertEqual(feedback.rating, "incorrect")
        self.assertEqual(feedback.category, "wrong_data")
        self.assertEqual(feedback.comment, "The price is stale.")

    def test_set_feedback_twice_upserts_not_duplicates(self):
        """A later call for the same message replaces the earlier one —
        the trader changing their mind, not a growing reaction list."""
        repo = self._repo()
        session = repo.create_session("AAPL")
        message = repo.add_message(session.id, "assistant", "AAPL is bullish.")

        repo.set_feedback(message.id, "incorrect", "wrong_data")
        repo.set_feedback(message.id, "correct")

        self.assertEqual(repo.db.query(ChatFeedback).filter(ChatFeedback.message_id == message.id).count(), 1)
        latest = repo.db.query(ChatFeedback).filter(ChatFeedback.message_id == message.id).first()
        self.assertEqual(latest.rating, "correct")
        self.assertIsNone(latest.category)

    def test_get_feedback_for_messages_batches_and_keys_by_message_id(self):
        repo = self._repo()
        session = repo.create_session("AAPL")
        m1 = repo.add_message(session.id, "assistant", "first")
        m2 = repo.add_message(session.id, "assistant", "second")
        m3 = repo.add_message(session.id, "assistant", "third")  # no feedback
        repo.set_feedback(m1.id, "correct")
        repo.set_feedback(m2.id, "not_useful", "poor_explanation")

        result = repo.get_feedback_for_messages([m1.id, m2.id, m3.id])
        self.assertEqual(set(result.keys()), {m1.id, m2.id})
        self.assertEqual(result[m1.id].rating, "correct")
        self.assertEqual(result[m2.id].category, "poor_explanation")

    def test_get_feedback_for_messages_empty_list_returns_empty_dict(self):
        repo = self._repo()
        self.assertEqual(repo.get_feedback_for_messages([]), {})

    def test_get_message_returns_none_for_missing_id(self):
        repo = self._repo()
        self.assertIsNone(repo.get_message(999))

    def test_negative_feedback_can_be_promoted_to_fixture_once(self):
        repo = self._repo()
        session = repo.create_session("AAPL")
        repo.add_message(session.id, "user", "How is AAPL?")
        answer = repo.add_message(session.id, "assistant", "AAPL is bullish.", response_blocks=[{"type": "prose"}])
        repo.set_feedback(answer.id, "incorrect", "wrong_data", "The quote was stale")

        fixture = repo.create_regression_fixture(answer.id)
        self.assertIsNotNone(fixture)
        self.assertEqual(fixture.prompt, "How is AAPL?")
        self.assertEqual(repo.create_regression_fixture(answer.id).id, fixture.id)

    def test_notebooks_round_trip_items_and_client_isolation(self):
        repo = self._repo()
        notebook = repo.create_notebook("client-a-123456", "Trade ideas")
        message = repo.add_message(repo.create_session("AAPL").id, "assistant", "AAPL is strong.", response_blocks=[])
        item = repo.save_notebook_item(
            notebook.id,
            message_id=message.id,
            question="How is AAPL?",
            answer=message.content,
            response_blocks=[],
            symbols=["AAPL"],
            content_types=["prose"],
            evidence_timestamps=[],
            stale=False,
        )
        self.assertEqual(item.question, "How is AAPL?")
        self.assertEqual(repo.list_notebooks("client-a-123456")[0].items[0].id, item.id)
        self.assertEqual(repo.list_notebooks("client-b-123456"), [])


if __name__ == "__main__":
    unittest.main()
