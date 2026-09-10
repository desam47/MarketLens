"""add ai_commentary column to alert_triggers

Revision ID: 20260909_alert_ai_comment
Revises: 20260909_ai_digests
Create Date: 2026-09-09 21:45:00.000000+00:00

AI feature 3 (Version 4): alert-triggered AI commentary. A short
AI-generated note explaining why an alert fired, populated
asynchronously after the trigger row commits — see
backend/models/alert.py and backend/ai/alert_commentary.py.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sqla_inspect


revision: str = '20260909_alert_ai_comment'
down_revision: Union[str, None] = '20260909_ai_digests'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sqla_inspect(conn)
    columns = [c['name'] for c in insp.get_columns('alert_triggers')]
    if 'ai_commentary' not in columns:
        op.add_column('alert_triggers', sa.Column('ai_commentary', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('alert_triggers', 'ai_commentary')
