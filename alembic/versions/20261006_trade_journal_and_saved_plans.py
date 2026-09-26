"""Add trade_journal_entries and saved_trade_plans tables.

Revision ID: 20261006_trade_journal_and_saved_plans
Revises: 20261005_flip_outcome_computed
Create Date: 2026-10-06
"""

from alembic import op
import sqlalchemy as sa

revision = "20261006_trade_journal_and_saved_plans"
down_revision = "20261005_flip_outcome_computed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trade_journal_entries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.String(64), nullable=False, unique=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("side", sa.String(5), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="planned"),
        sa.Column("entry_date", sa.String(32), nullable=False),
        sa.Column("exit_date", sa.String(32), nullable=True),
        sa.Column("quantity", sa.Float, nullable=False),
        sa.Column("entry_price", sa.Float, nullable=False),
        sa.Column("exit_price", sa.Float, nullable=True),
        sa.Column("stop_price", sa.Float, nullable=True),
        sa.Column("target_price", sa.Float, nullable=True),
        sa.Column("thesis", sa.Text, nullable=False, server_default=""),
        sa.Column("review_notes", sa.Text, nullable=False, server_default=""),
        sa.Column("signal_context_json", sa.Text, nullable=True),
        sa.Column("market_context_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_trade_journal_entries_client_id", "trade_journal_entries", ["client_id"])
    op.create_index("ix_trade_journal_entries_symbol", "trade_journal_entries", ["symbol"])

    op.create_table(
        "saved_trade_plans",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.String(64), nullable=False, unique=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("side", sa.String(5), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("entry_price", sa.Float, nullable=False),
        sa.Column("stop_price", sa.Float, nullable=False),
        sa.Column("target_price", sa.Float, nullable=False),
        sa.Column("quantity", sa.Float, nullable=False),
        sa.Column("stop_source", sa.String(50), nullable=False, server_default=""),
        sa.Column("reward_risk", sa.Float, nullable=True),
        sa.Column("thesis", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_saved_trade_plans_client_id", "saved_trade_plans", ["client_id"])
    op.create_index("ix_saved_trade_plans_symbol", "saved_trade_plans", ["symbol"])


def downgrade() -> None:
    op.drop_table("saved_trade_plans")
    op.drop_table("trade_journal_entries")
