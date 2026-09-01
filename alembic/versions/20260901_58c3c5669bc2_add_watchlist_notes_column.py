"""add watchlist notes column

Revision ID: 58c3c5669bc2
Revises: 8175af1a213e
Create Date: 2026-09-01 02:09:05.348243+00:00

Adds the ``notes`` column to ``watchlist_symbols`` that was added to the
SQLAlchemy model but never migrated to the live database. This caused
500s on every write to the table (add, reorder, update).

The column was already added manually to the running DB on 2026-09-01
to restore watchlist add functionality. This revision is recorded as
applied on next ``alembic upgrade head`` if not already present.

NOTE: SQLite ALTER TABLE ADD COLUMN is not idempotent — re-running this
revision on a DB that already has the column will fail. The production
deployment workflow assumes the runner is invoked once per release; if
you need to be safe, do a schema check before running.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '58c3c5669bc2'
down_revision: Union[str, None] = '8175af1a213e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('watchlist_symbols', sa.Column('notes', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('watchlist_symbols', 'notes')
