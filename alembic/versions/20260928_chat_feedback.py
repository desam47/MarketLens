"""add chat_feedback table (5.7.8 feedback and correction loop)"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_chat_feedback"
down_revision: str | None = "20260927_chat_response_blocks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "chat_feedback" in inspector.get_table_names():
        return
    op.create_table(
        "chat_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("chat_messages.id"), nullable=False),
        sa.Column("rating", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_chat_feedback_id", "chat_feedback", ["id"])
    op.create_index("ix_chat_feedback_message_id", "chat_feedback", ["message_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_chat_feedback_message_id", table_name="chat_feedback")
    op.drop_index("ix_chat_feedback_id", table_name="chat_feedback")
    op.drop_table("chat_feedback")
