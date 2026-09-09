"""add session column to bars

Revision ID: 20260909_bar_session
Revises: 20260908_backfill_jobs
Create Date: 2026-09-09 09:55:00.000000+00:00

Adds ``session`` to ``bars`` — equity session classification ('premarket' /
'regular' / 'after_hours', matching backend.engines.market_calendar.SessionType).

Enables storing pre-market/after-hours 1m bars (confirmed available via
Webull's ``trading_sessions`` param — see webull_provider.py's
``include_extended_hours``) without changing what sub-hour/higher-timeframe
resampling reads: ``_resample_and_upsert`` filters on
``session == 'regular'``, so 2m/3m/5m/15m/30m/1h/4h/1d/1wk are unaffected.

``server_default='regular'`` means every pre-existing row (all ingestion to
date has been RTH-only) is correctly classified with no backfill UPDATE
needed — SQLite applies a column default to existing rows transparently.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sqla_inspect


revision: str = '20260909_bar_session'
down_revision: Union[str, None] = '20260908_backfill_jobs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sqla_inspect(conn)
    columns = [c['name'] for c in insp.get_columns('bars')]
    if 'session' not in columns:
        op.add_column(
            'bars',
            sa.Column('session', sa.String(20), nullable=False, server_default='regular'),
        )


def downgrade() -> None:
    op.drop_column('bars', 'session')
