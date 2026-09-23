"""add persisted typed Chat workflows"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260926_chat_workflows"
down_revision: str | None = "20260925_chat_planner_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_workflows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("steps", sa.Text(), nullable=False),
        sa.Column("parameters", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("output_layout", sa.Text(), nullable=False, server_default="summary"),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_chat_workflows_name", "chat_workflows", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_chat_workflows_name", table_name="chat_workflows")
    op.drop_table("chat_workflows")
