"""add ai_digests table

Revision ID: 20260909_ai_digests
Revises: 20260909_bar_session
Create Date: 2026-09-09 21:20:00.000000+00:00

AI feature 2 (Version 4): daily/session AI digest. One row per
generated digest run — see backend/models/ai_digest.py.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sqla_inspect


revision: str = '20260909_ai_digests'
down_revision: Union[str, None] = '20260909_bar_session'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sqla_inspect(conn)
    if 'ai_digests' in insp.get_table_names():
        return

    op.create_table(
        'ai_digests',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('session', sa.String(20), nullable=False),
        sa.Column('generated_at', sa.DateTime(), nullable=False),
        sa.Column('market_regime', sa.String(20), nullable=True),
        sa.Column('narrative', sa.Text(), nullable=True),
        sa.Column('payload', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index(
        'ix_ai_digests_session_generated', 'ai_digests',
        ['session', 'generated_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_ai_digests_session_generated', table_name='ai_digests')
    op.drop_table('ai_digests')
