"""add alert notification delivery attempts

Revision ID: 20260920_alert_deliveries
Revises: 20260920_signal_profile_alerts
Create Date: 2026-09-20
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260920_alert_deliveries"
down_revision: str | None = "20260920_signal_profile_alerts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trigger_id", sa.Integer(), sa.ForeignKey("alert_triggers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_alert_deliveries_trigger_id", "alert_deliveries", ["trigger_id"])
    op.create_index("ix_alert_deliveries_status", "alert_deliveries", ["status"])


def downgrade() -> None:
    op.drop_index("ix_alert_deliveries_status", table_name="alert_deliveries")
    op.drop_index("ix_alert_deliveries_trigger_id", table_name="alert_deliveries")
    op.drop_table("alert_deliveries")
