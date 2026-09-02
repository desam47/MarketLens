"""add watchlist entity_type column

Revision ID: 2e3f4a5b6c7d
Revises: a3b1c2d4e5f6
Create Date: 2026-09-01 20:30:00.000000+00:00

Adds the ``entity_type`` column to ``watchlist_symbols`` so symbols can be
classified as "stock" or "etf". Nullable by default so existing rows stay valid.

The column was already added to the running DB to avoid 500s on next write.
The op.execute + CHECK guards make this migration idempotent on both SQLite
(and PostgreSQL if that becomes the production target).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sqla_inspect


revision: str = '2e3f4a5b6c7d'
down_revision: Union[str, None] = '7f2e3a1b8c4d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite: ADD COLUMN is idempotent IF the column doesn't exist.
    # Guard with a pragma check so re-runs don't raise.
    conn = op.get_bind()
    insp = sqla_inspect(conn)
    columns = [c['name'] for c in insp.get_columns('watchlist_symbols')]
    if 'entity_type' not in columns:
        op.add_column('watchlist_symbols', sa.Column('entity_type', sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column('watchlist_symbols', 'entity_type')
