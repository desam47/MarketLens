"""persist provider observability events"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260923_provider_events"
down_revision: str | None = "20260922_schema_complete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "provider_events" in inspector.get_table_names():
        return
    op.create_table(
        "provider_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("method", sa.String(length=100), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_provider_events_timestamp", "provider_events", ["timestamp"])
    op.create_index(
        "ix_provider_events_provider_timestamp", "provider_events", ["provider", "timestamp"]
    )
    op.create_index(
        "ix_provider_events_outcome_timestamp", "provider_events", ["outcome", "timestamp"]
    )


def downgrade() -> None:
    op.drop_index("ix_provider_events_outcome_timestamp", table_name="provider_events")
    op.drop_index("ix_provider_events_provider_timestamp", table_name="provider_events")
    op.drop_index("ix_provider_events_timestamp", table_name="provider_events")
    op.drop_table("provider_events")
