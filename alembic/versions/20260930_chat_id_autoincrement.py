"""never reuse chat session/message ids (SQLite AUTOINCREMENT)

Without AUTOINCREMENT, SQLite hands out max(rowid)+1, so clearing the chat
(which deletes the newest rows) made new messages reuse deleted ids. Rows
that still referenced those ids — chat_feedback, chat_regression_fixtures,
research_notebook_items — then attached to the wrong new message: an old
rating showed on a new answer, fixture promotion hit UNIQUE(message_id), and
saving a new answer to a notebook overwrote an older saved item.

This rebuilds chat_sessions and chat_messages with AUTOINCREMENT, drops
feedback rows whose message no longer exists, and seeds sqlite_sequence
above every message id still referenced anywhere, so no new message can
take an id another table points at.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260930_chat_id_autoincrement"
down_revision: str | None = "20260929_chat_notebooks_fixtures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("chat_sessions", "chat_messages")

# Every column that holds a chat_messages.id. A new message id must be
# above all of them, not just above the surviving messages.
_MESSAGE_ID_REFERENCES = (
    ("chat_messages", "id"),
    ("chat_feedback", "message_id"),
    ("chat_regression_fixtures", "message_id"),
    ("research_notebook_items", "message_id"),
)


def _has_autoincrement(bind, table: str) -> bool:
    sql = bind.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :t"), {"t": table}
    ).scalar()
    return bool(sql) and "AUTOINCREMENT" in sql.upper()


def _max(bind, table: str, column: str, tables: set[str]) -> int:
    if table not in tables:
        return 0
    return int(bind.execute(sa.text(f"SELECT COALESCE(MAX({column}), 0) FROM {table}")).scalar() or 0)


def _seed_sequence(bind, table: str, seq: int) -> None:
    bind.execute(sa.text("DELETE FROM sqlite_sequence WHERE name = :t"), {"t": table})
    bind.execute(sa.text("INSERT INTO sqlite_sequence (name, seq) VALUES (:t, :s)"), {"t": table, "s": seq})


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return  # other databases' sequences never reuse ids
    tables = set(sa.inspect(bind).get_table_names())

    if {"chat_feedback", "chat_messages"} <= tables:
        # Feedback on a deleted message would otherwise surface on whichever
        # new message reused its id.
        op.execute("DELETE FROM chat_feedback WHERE message_id NOT IN (SELECT id FROM chat_messages)")

    for table in _TABLES:
        if table in tables and not _has_autoincrement(bind, table):
            with op.batch_alter_table(table, recreate="always", table_kwargs={"sqlite_autoincrement": True}):
                pass

    if "chat_sessions" in tables:
        _seed_sequence(bind, "chat_sessions", _max(bind, "chat_sessions", "id", tables))
    if "chat_messages" in tables:
        seq = max(_max(bind, table, column, tables) for table, column in _MESSAGE_ID_REFERENCES)
        _seed_sequence(bind, "chat_messages", seq)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    tables = set(sa.inspect(bind).get_table_names())
    for table in reversed(_TABLES):
        if table in tables and _has_autoincrement(bind, table):
            # Reflection does not carry sqlite_autoincrement, so a plain
            # rebuild drops it. Deleted orphan feedback is not restored.
            with op.batch_alter_table(table, recreate="always"):
                pass
