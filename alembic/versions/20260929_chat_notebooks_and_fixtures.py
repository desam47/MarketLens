"""add approved chat fixtures and server-backed research notebooks"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260929_chat_notebooks_fixtures"
down_revision: str | None = "20260928_chat_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "chat_regression_fixtures" not in tables:
        op.create_table(
            "chat_regression_fixtures",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("message_id", sa.Integer(), sa.ForeignKey("chat_messages.id"), nullable=False, unique=True),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("response", sa.Text(), nullable=False),
            sa.Column("response_blocks", sa.Text(), nullable=True),
            sa.Column("rating", sa.String(length=16), nullable=False),
            sa.Column("category", sa.String(length=32), nullable=True),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="approved"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_chat_regression_fixtures_id", "chat_regression_fixtures", ["id"])
        op.create_index("ix_chat_regression_fixtures_message_id", "chat_regression_fixtures", ["message_id"], unique=True)
    if "research_notebooks" not in tables:
        op.create_table(
            "research_notebooks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_key", sa.String(length=80), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_research_notebooks_id", "research_notebooks", ["id"])
        op.create_index("ix_research_notebooks_client_key", "research_notebooks", ["client_key"])
    if "research_notebook_items" not in tables:
        op.create_table(
            "research_notebook_items",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("notebook_id", sa.Integer(), sa.ForeignKey("research_notebooks.id"), nullable=False),
            sa.Column("message_id", sa.Integer(), nullable=False),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("answer", sa.Text(), nullable=False),
            sa.Column("response_blocks", sa.Text(), nullable=True),
            sa.Column("symbols", sa.Text(), nullable=True),
            sa.Column("content_types", sa.Text(), nullable=True),
            sa.Column("evidence_timestamps", sa.Text(), nullable=True),
            sa.Column("stale", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("material_change_detected", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_research_notebook_items_id", "research_notebook_items", ["id"])
        op.create_index("ix_research_notebook_items_notebook_id", "research_notebook_items", ["notebook_id"])
        op.create_index("ix_research_notebook_items_message_id", "research_notebook_items", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_research_notebook_items_message_id", table_name="research_notebook_items")
    op.drop_index("ix_research_notebook_items_notebook_id", table_name="research_notebook_items")
    op.drop_index("ix_research_notebook_items_id", table_name="research_notebook_items")
    op.drop_table("research_notebook_items")
    op.drop_index("ix_research_notebooks_client_key", table_name="research_notebooks")
    op.drop_index("ix_research_notebooks_id", table_name="research_notebooks")
    op.drop_table("research_notebooks")
    op.drop_index("ix_chat_regression_fixtures_message_id", table_name="chat_regression_fixtures")
    op.drop_index("ix_chat_regression_fixtures_id", table_name="chat_regression_fixtures")
    op.drop_table("chat_regression_fixtures")
