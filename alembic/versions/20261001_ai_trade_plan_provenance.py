"""add verified-plan provenance to tracked AI setups

Revision ID: 20261001_ai_trade_plan_provenance
Revises: 20260930_chat_id_autoincrement
Create Date: 2026-10-01 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261001_ai_trade_plan_provenance"
down_revision: str | None = "20260930_chat_id_autoincrement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("ai_trade_plan_outcomes")}
    if "timeframe" not in columns:
        op.add_column(
            "ai_trade_plan_outcomes",
            sa.Column("timeframe", sa.String(length=8), nullable=False, server_default="1d"),
        )
    if "analysis_id" not in columns:
        op.add_column(
            "ai_trade_plan_outcomes",
            sa.Column("analysis_id", sa.String(length=64), nullable=False, server_default=""),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("ai_trade_plan_outcomes")}
    if "analysis_id" in columns:
        op.drop_column("ai_trade_plan_outcomes", "analysis_id")
    if "timeframe" in columns:
        op.drop_column("ai_trade_plan_outcomes", "timeframe")
