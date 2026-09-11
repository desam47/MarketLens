"""add ai_trade_plan_outcomes table

Revision ID: 20260911_ai_trade_plan_outcomes
Revises: 20260910_tape_bars
Create Date: 2026-09-11 00:00:00.000000+00:00

Outcome tracking for the AI's own buy/sell trade_plan calls
(2026-09-11) — one row per actionable recommendation, captured at
analyze_symbol()'s single choke point and graded later by a
background pass against daily bars. Guarded create_table so a re-run
is a no-op — matches every other migration in this repo.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sqla_inspect


revision: str = '20260911_ai_trade_plan_outcomes'
down_revision: Union[str, None] = '20260910_tape_bars'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sqla_inspect(conn)
    if 'ai_trade_plan_outcomes' not in insp.get_table_names():
        op.create_table(
            'ai_trade_plan_outcomes',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('symbol', sa.String(20), nullable=False),
            sa.Column('recommendation', sa.String(10), nullable=False),
            sa.Column('conviction', sa.String(10), nullable=False),
            sa.Column('time_horizon', sa.String(10), nullable=False),
            sa.Column('entry_zone_low', sa.Float(), nullable=True),
            sa.Column('entry_zone_high', sa.Float(), nullable=True),
            sa.Column('stop_loss', sa.Float(), nullable=True),
            sa.Column('targets_json', sa.Text(), nullable=False, server_default='[]'),
            sa.Column('risk_reward', sa.Float(), nullable=True),
            sa.Column('provider', sa.String(50), nullable=False),
            sa.Column('model', sa.String(100), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('status', sa.String(12), nullable=False, server_default='open'),
            sa.Column('resolved_at', sa.DateTime(), nullable=True),
            sa.Column('resolved_price', sa.Float(), nullable=True),
            sa.Column('hit_target_index', sa.Integer(), nullable=True),
            sa.Column('return_pct', sa.Float(), nullable=True),
        )
        op.create_index('ix_ai_trade_plan_outcomes_id', 'ai_trade_plan_outcomes', ['id'])
        op.create_index('ix_ai_trade_plan_outcomes_symbol', 'ai_trade_plan_outcomes', ['symbol'])
        op.create_index('ix_ai_trade_plan_outcomes_created_at', 'ai_trade_plan_outcomes', ['created_at'])
        op.create_index('ix_ai_trade_plan_outcomes_status', 'ai_trade_plan_outcomes', ['status'])
        op.create_index(
            'ix_ai_trade_plan_outcomes_symbol_status', 'ai_trade_plan_outcomes',
            ['symbol', 'status'],
        )


def downgrade() -> None:
    op.drop_table('ai_trade_plan_outcomes')
