"""restore missing feature tables and model indexes

Revision ID: 20260922_schema_complete
Revises: 20260921_alert_delivery_idempotency
Create Date: 2026-09-22

Several feature models were created through ``Base.metadata.create_all`` in
local development, but never received Alembic migrations. A fresh database
therefore lacked the tables even after ``alembic upgrade head``. This
compatibility migration creates the missing tables only when absent and brings
the remaining ORM-index drift back in sync.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260922_schema_complete"
down_revision: str | None = "20260921_alert_delivery_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def _ensure_index(
    table_name: str,
    index_name: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    if not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "ai_analysis_jobs" not in tables:
        op.create_table(
            "ai_analysis_jobs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("job_id", sa.String(length=64), nullable=False),
            sa.Column("symbol", sa.String(length=10), nullable=False),
            sa.Column("timeframe", sa.String(length=10), nullable=False),
            sa.Column("template_id", sa.Integer(), nullable=True),
            sa.Column("template_name", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("result", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )
    _ensure_index("ai_analysis_jobs", "ix_ai_analysis_jobs_job_id", ["job_id"], unique=True)
    _ensure_index("ai_analysis_jobs", "ix_ai_analysis_jobs_status", ["status"])
    _ensure_index(
        "ai_analysis_jobs",
        "ix_ai_analysis_jobs_symbol_created",
        ["symbol", "created_at"],
    )

    if "ai_templates" not in tables:
        op.create_table(
            "ai_templates",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("system_prompt", sa.Text(), nullable=False),
            sa.Column("user_instructions", sa.Text(), nullable=True),
            sa.Column("variables_json", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("is_default", sa.Boolean(), nullable=False),
            sa.Column("is_system", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    _ensure_index("ai_templates", "ix_ai_templates_id", ["id"])

    if "custom_indicators" not in tables:
        op.create_table(
            "custom_indicators",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("watchlist_id", sa.Integer(), nullable=True),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("slug", sa.String(length=50), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("formula_type", sa.String(length=30), nullable=False),
            sa.Column("parameters", sa.Text(), nullable=True),
            sa.Column("color", sa.String(length=7), nullable=True),
            sa.Column("line_width", sa.Float(), nullable=True),
            sa.Column("line_style", sa.String(length=20), nullable=True),
            sa.Column("separate_pane", sa.Boolean(), nullable=False),
            sa.Column("pane_height", sa.Integer(), nullable=True),
            sa.Column("is_overlay", sa.Boolean(), nullable=False),
            sa.Column("z_index", sa.Integer(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["watchlist_id"], ["watchlists.id"]),
        )
    _ensure_index("custom_indicators", "ix_custom_indicators_id", ["id"])
    _ensure_index("custom_indicators", "ix_custom_indicators_slug", ["slug"], unique=True)

    if "drawing_tools" not in tables:
        op.create_table(
            "drawing_tools",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("watchlist_id", sa.Integer(), nullable=True),
            sa.Column("symbol", sa.String(length=10), nullable=False),
            sa.Column("timeframe", sa.String(length=10), nullable=False),
            sa.Column("drawing_type", sa.String(length=30), nullable=False),
            sa.Column("label", sa.String(length=200), nullable=True),
            sa.Column("color", sa.String(length=7), nullable=True),
            sa.Column("line_width", sa.Float(), nullable=True),
            sa.Column("line_style", sa.String(length=20), nullable=True),
            sa.Column("font_size", sa.Integer(), nullable=True),
            sa.Column("opacity", sa.Float(), nullable=True),
            sa.Column("start_timestamp", sa.String(length=30), nullable=False),
            sa.Column("start_price", sa.Float(), nullable=False),
            sa.Column("end_timestamp", sa.String(length=30), nullable=True),
            sa.Column("end_price", sa.Float(), nullable=True),
            sa.Column("fib_levels", sa.String(length=200), nullable=True),
            sa.Column("top_price", sa.Float(), nullable=True),
            sa.Column("bottom_price", sa.Float(), nullable=True),
            sa.Column("is_visible", sa.Boolean(), nullable=False),
            sa.Column("is_locked", sa.Boolean(), nullable=False),
            sa.Column("extend_left", sa.Boolean(), nullable=False),
            sa.Column("extend_right", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["watchlist_id"], ["watchlists.id"]),
        )
    _ensure_index("drawing_tools", "ix_drawing_tools_id", ["id"])
    _ensure_index("drawing_tools", "ix_drawing_tools_symbol", ["symbol"])

    _ensure_index("ai_digests", "ix_ai_digests_session", ["session"])
    _ensure_index("alert_deliveries", "ix_alert_deliveries_id", ["id"])
    _ensure_index("chat_sessions", "ix_chat_sessions_scope", ["scope"])

    if _has_index("ai_trade_plan_outcomes", "ix_ai_trade_plan_outcomes_id"):
        op.drop_index("ix_ai_trade_plan_outcomes_id", table_name="ai_trade_plan_outcomes")


def downgrade() -> None:
    """Keep the compatibility repair forward-only to preserve existing local data."""
    return None
