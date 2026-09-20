"""add idempotency keys to alert deliveries

Revision ID: 20260921_alert_delivery_idempotency
Revises: 20260920_alert_deliveries
Create Date: 2026-09-21
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260921_alert_delivery_idempotency"
down_revision: str | None = "20260920_alert_deliveries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("alert_deliveries") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=120), nullable=True))
        batch_op.create_index(
            "ix_alert_deliveries_idempotency_key",
            ["idempotency_key"],
            unique=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("alert_deliveries") as batch_op:
        batch_op.drop_index("ix_alert_deliveries_idempotency_key")
        batch_op.drop_column("idempotency_key")
