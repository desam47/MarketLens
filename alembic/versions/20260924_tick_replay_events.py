"""persist bounded tick replay events"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260924_tick_replay_events"
down_revision: str | None = "20260923_provider_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "tick_replay_events" in inspector.get_table_names():
        return
    op.create_table(
        "tick_replay_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("event_timestamp", sa.String(length=64), nullable=True),
        sa.Column("received_at", sa.Float(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_tick_replay_symbol_received", "tick_replay_events", ["symbol", "received_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_tick_replay_symbol_received", table_name="tick_replay_events")
    op.drop_table("tick_replay_events")
