"""cleanup duplicate/redundant indexes + add bar-count column

Revision ID: 20260902_index_cleanup
Revises: 2e3f4a5b6c7d
Create Date: 2026-09-02

Phase 3.3.4: index audit + cleanup.

Audit findings (EXPLAIN QUERY PLAN on hot queries):

  ix_bars_symbol_timeframe_timestamp (unique):  used for upsert + 1m fast path. KEEP.
  ix_bars_symbol:                    used for bulk DELETE WHERE symbol IN (...).   KEEP.
  ix_bars_timestamp:                 used for prune DELETE WHERE timestamp < X.   KEEP.
  ix_bars_timeframe_timestamp:       used for timeframe-only count / list.       KEEP.
  ix_bars_source_timeframe:          used for "resampled count" metric.           KEEP.
  ix_bars_provider_symbol:          used for provider+symbol diagnostic query.   KEEP.
  ix_bars_id:                        auto-created by Column(..., primary_key=True,
                                        index=True); PK already indexed.          DROP.
  ix_bars_timeframe:                 covered by ix_bars_timeframe_timestamp prefix.
                                                                       DROP.

The redundant indexes consume WAL write overhead (every row insert updates
both the PK index and ix_bars_id) without serving any query.  Dropping
them reduces write amplification and shrinks the DB file.

alerts table: the ix_alerts_is_enabled single-column index already
serves the (symbol, is_enabled) query path well (~10 alert rows in
practice). The composite (symbol, is_enabled) index is not worth the
added write cost.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260902_index_cleanup'
down_revision: Union[str, None] = '2e3f4a5b6c7d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop redundant auto-created PK indexes.
    # SQLite does not support DROP INDEX IF EXISTS in the same step as
    # CREATE — but it is supported in Alembic's op.execute() context.
    op.execute("DROP INDEX IF EXISTS ix_quotes_id")
    op.execute("DROP INDEX IF EXISTS ix_bars_id")
    op.execute("DROP INDEX IF EXISTS ix_market_status_id")
    op.execute("DROP INDEX IF EXISTS ix_provider_status_id")

    # Drop ix_bars_timeframe — covered by ix_bars_timeframe_timestamp prefix.
    # (A two-column index can satisfy queries on the leading column alone.)
    op.execute("DROP INDEX IF EXISTS ix_bars_timeframe")


def downgrade() -> None:
    # Restore ix_bars_timeframe (timeframe-only queries fall back to
    # ix_bars_timeframe_timestamp if this is absent).
    op.create_index(
        'ix_bars_timeframe',
        'bars',
        ['timeframe'],
        if_not_exists=True,
    )
    # Restore auto-PK indexes — harmless to recreate even if the model
    # is fixed to remove index=True from PK columns later.
    op.create_index('ix_quotes_id', 'quotes', ['id'], if_not_exists=True)
    op.create_index('ix_bars_id', 'bars', ['id'], if_not_exists=True)
    op.create_index('ix_market_status_id', 'market_status', ['id'], if_not_exists=True)
    op.create_index('ix_provider_status_id', 'provider_status', ['id'], if_not_exists=True)
