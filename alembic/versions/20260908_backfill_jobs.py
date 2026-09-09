"""add backfill_jobs table

Revision ID: 20260908_backfill_jobs
Revises: 20260902_index_cleanup
Create Date: 2026-09-08

Backs the rebuilt ticker-add pipeline: a single RQ job (instead of two
racing in-process triggers) now owns backfill for a newly-added symbol,
and this table is its pollable status row — mirrors how ai_analysis_jobs
backs the existing AI-analysis RQ queue.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sqla_inspect

# revision identifiers, used by Alembic.
revision: str = '20260908_backfill_jobs'
down_revision: Union[str, None] = '20260902_index_cleanup'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sqla_inspect(conn)
    if 'backfill_jobs' in insp.get_table_names():
        return

    op.create_table(
        'backfill_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.String(length=64), nullable=False),
        sa.Column('symbol', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='queued'),
        sa.Column('tier1_written', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tier2_written', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tier3_written', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('gaps_found', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('gaps_filled', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('result', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_backfill_jobs_job_id', 'backfill_jobs', ['job_id'], unique=True)
    op.create_index('ix_backfill_jobs_symbol', 'backfill_jobs', ['symbol'], unique=False)
    op.create_index('ix_backfill_jobs_status', 'backfill_jobs', ['status'], unique=False)
    op.create_index(
        'ix_backfill_jobs_symbol_created', 'backfill_jobs', ['symbol', 'created_at'], unique=False,
    )


def downgrade() -> None:
    op.drop_table('backfill_jobs')
