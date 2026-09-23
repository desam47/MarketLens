"""persist structured Chat planner state"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260925_chat_planner_state"
down_revision: str | None = "20260924_tick_replay_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("chat_sessions")}
    if "planner_state" not in columns:
        op.add_column("chat_sessions", sa.Column("planner_state", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_sessions", "planner_state")
