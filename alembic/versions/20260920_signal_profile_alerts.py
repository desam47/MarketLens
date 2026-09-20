"""allow structured signal-profile alert parameters

Revision ID: 20260920_signal_profile_alerts
Revises: 20260919_index_tuning
Create Date: 2026-09-20
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260920_signal_profile_alerts"
down_revision: str | None = "20260919_index_tuning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("alerts") as batch_op:
        batch_op.alter_column("parameter", existing_type=sa.String(length=120), type_=sa.String(length=500), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("alerts") as batch_op:
        batch_op.alter_column("parameter", existing_type=sa.String(length=500), type_=sa.String(length=120), existing_nullable=False)
