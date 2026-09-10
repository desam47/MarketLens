"""add chat_sessions and chat_messages tables

Revision ID: 20260909_chat_sessions
Revises: 20260909_alert_ai_comment
Create Date: 2026-09-09 22:05:00.000000+00:00

AI feature 4 (Version 4): conversational AI chat panel. Mirrors the
Alert/AlertTrigger parent/child shape — see backend/models/chat.py.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sqla_inspect


revision: str = '20260909_chat_sessions'
down_revision: Union[str, None] = '20260909_alert_ai_comment'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sqla_inspect(conn)
    existing = insp.get_table_names()

    if 'chat_sessions' not in existing:
        op.create_table(
            'chat_sessions',
            sa.Column('id', sa.Integer(), primary_key=True, index=True),
            sa.Column('symbol', sa.String(20), nullable=False),
            sa.Column('alert_trigger_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_chat_sessions_symbol', 'chat_sessions', ['symbol'])

    if 'chat_messages' not in existing:
        op.create_table(
            'chat_messages',
            sa.Column('id', sa.Integer(), primary_key=True, index=True),
            sa.Column('session_id', sa.Integer(), sa.ForeignKey('chat_sessions.id'), nullable=False),
            sa.Column('role', sa.String(10), nullable=False),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_chat_messages_session_id', 'chat_messages', ['session_id'])
        op.create_index('ix_chat_messages_created_at', 'chat_messages', ['created_at'])


def downgrade() -> None:
    op.drop_table('chat_messages')
    op.drop_table('chat_sessions')
