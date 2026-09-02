"""rename bars index ix_bars_timeframe_source -> ix_bars_source_timeframe

Revision ID: 7f2e3a1b8c4d
Revises: a3b1c2d4e5f6
Create Date: 2026-09-01 19:00:00.000000+00:00

Phase 3.1: rename the composite index on ``bars`` from
``ix_bars_timeframe_source`` (timeframe, source) to
``ix_bars_source_timeframe`` (source, timeframe).

The (source, timeframe) column order is better for Phase 3.1 queries
because the leading column (source='raw') has higher selectivity — all
stored rows have source='raw', but the next predicate (timeframe='1m')
further narrows the scan. The reverse order would scan all 1m rows
before applying the source filter.

SQLite does not support ALTER INDEX RENAME, so the migration drops the
old index and creates the new one in a single upgrade step.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7f2e3a1b8c4d'
down_revision: Union[str, None] = 'a3b1c2d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite: DROP then CREATE (no RENAME INDEX).
    op.drop_index('ix_bars_timeframe_source', table_name='bars')
    op.create_index(
        'ix_bars_source_timeframe',
        'bars',
        ['source', 'timeframe'],
    )


def downgrade() -> None:
    # Revert to the previous index.
    op.drop_index('ix_bars_source_timeframe', table_name='bars')
    op.create_index(
        'ix_bars_timeframe_source',
        'bars',
        ['timeframe', 'source'],
    )
