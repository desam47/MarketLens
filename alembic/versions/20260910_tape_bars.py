"""add tape_bars table

Revision ID: 20260910_tape_bars
Revises: 20260910_chat_session_scope
Create Date: 2026-09-10 17:00:00.000000+00:00

1-second Time & Sales aggregates for the tape-analytics engine
(2026-09-10). Raw trade prints stay in memory; only these per-second
buckets are persisted (short retention). Guarded create_table so a
re-run is a no-op — matches every other migration in this repo.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sqla_inspect


revision: str = '20260910_tape_bars'
down_revision: Union[str, None] = '20260910_chat_session_scope'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sqla_inspect(conn)
    if 'tape_bars' not in insp.get_table_names():
        op.create_table(
            'tape_bars',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('symbol', sa.String(20), nullable=False),
            sa.Column('timestamp', sa.DateTime(), nullable=False),
            sa.Column('open', sa.Float(), nullable=False),
            sa.Column('high', sa.Float(), nullable=False),
            sa.Column('low', sa.Float(), nullable=False),
            sa.Column('close', sa.Float(), nullable=False),
            sa.Column('volume', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('buy_volume', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('sell_volume', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('signed_volume', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('trade_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('block_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('vwap', sa.Float(), nullable=True),
            sa.Column('provider', sa.String(50), nullable=False, server_default='webull_stream'),
        )
        op.create_index('ix_tape_bars_id', 'tape_bars', ['id'])
        op.create_index('ix_tape_bars_symbol', 'tape_bars', ['symbol'])
        op.create_index('ix_tape_bars_timestamp', 'tape_bars', ['timestamp'])
        op.create_index(
            'ix_tape_bars_symbol_timestamp', 'tape_bars', ['symbol', 'timestamp'], unique=True,
        )


def downgrade() -> None:
    op.drop_table('tape_bars')
