"""persist typed Chat response blocks"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_chat_response_blocks"
down_revision: str | None = "20260926_chat_workflows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("chat_messages")}
    if "response_blocks" not in columns:
        op.add_column("chat_messages", sa.Column("response_blocks", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "response_blocks")
