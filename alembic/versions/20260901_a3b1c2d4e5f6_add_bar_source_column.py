"""add bar source column

Revision ID: a3b1c2d4e5f6
Revises: 58c3c5669bc2
Create Date: 2026-09-01 18:00:00.000000+00:00

Phase 3.1: add the ``source`` column to ``bars`` so the API can
distinguish rows that came from a provider (raw) versus rows that
were derived from 1m at read time (resampled).

All existing rows are tagged 'raw' by default — the migration does
not retroactively mark any rows as resampled because resampling is
read-time only and does not write to the DB.

A composite index on (timeframe, source) is added so the cache
lookup pattern (find a stored 1m bar by source='raw') is index-backed.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b1c2d4e5f6'
down_revision: Union[str, None] = '58c3c5669bc2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'bars',
        sa.Column('source', sa.String(20), nullable=False, server_default='raw'),
    )
    op.create_index(
        'ix_bars_timeframe_source',
        'bars',
        ['timeframe', 'source'],
    )


def downgrade() -> None:
    op.drop_index('ix_bars_timeframe_source', table_name='bars')
    op.drop_column('bars', 'source')
