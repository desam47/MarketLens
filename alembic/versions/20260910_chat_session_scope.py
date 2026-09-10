"""add scope column to chat_sessions

Revision ID: 20260910_chat_session_scope
Revises: 20260909_chat_sessions
Create Date: 2026-09-10 13:30:00.000000+00:00

Universal AI Hub chat (2026-09-10). Adds ``scope`` to ``chat_sessions``
— 'universal' (no ticker; ``symbol`` holds the '*' sentinel), 'symbol'
(one thread per ticker, the legacy default), or 'alert' (opened from an
alert trigger row).

``server_default='symbol'`` classifies every pre-existing row correctly
with no backfill needed — SQLite applies a column default to existing
rows transparently — and the guarded ``UPDATE`` re-tags rows that were
opened from an alert trigger. ``symbol`` stays NOT NULL (universal rows
store '*'), so no table rebuild / batch_alter_table is required, matching
every other migration in this repo (see 20260909_bar_session_column.py).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sqla_inspect


revision: str = '20260910_chat_session_scope'
down_revision: Union[str, None] = '20260909_chat_sessions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sqla_inspect(conn)
    columns = [c['name'] for c in insp.get_columns('chat_sessions')]
    if 'scope' not in columns:
        op.add_column(
            'chat_sessions',
            sa.Column('scope', sa.String(16), nullable=False, server_default='symbol'),
        )
        op.execute(
            "UPDATE chat_sessions SET scope='alert' WHERE alert_trigger_id IS NOT NULL"
        )


def downgrade() -> None:
    op.drop_column('chat_sessions', 'scope')
