"""add ix_historical_signals_symbol / ix_historical_signals_timeframe if missing

The model (``backend/models/signal.py``) declares ``index=True`` on both
``symbol`` and ``timeframe``, and the initial-schema migration
(``20260828_8175af1a213e``) creates both — but a fresh ``alembic upgrade
head`` build and the live database diverged: the live ``historical_signals``
table has neither. All columns match; this is the only schema difference
found (MD-09, market-data review). No migration in this history drops them,
so the live table was likely created outside the migration chain at some
point (a manual rebuild or an early ``Base.metadata.create_all()``) before
they existed and nothing since has needed to touch this table's indexes in a
way that would have caught the drift.

Both stayed deliberately in the model when ``20260919_index_tuning`` dropped
other redundant single-column indexes elsewhere (that migration's own
docstring: "historical_signals keeps its symbol/timeframe indexes") — so the
right fix is to add them where missing, not drop them from the model, so the
live schema matches the migration history everywhere, not just here.

``CREATE INDEX IF NOT EXISTS`` — a no-op on a database that already has them
(a fresh ``alembic upgrade head`` build), and adds them on one that doesn't
(the live database). No data is touched.

Revision ID: 20261004_historical_signals_missing_indexes
Revises: 20261003_signal_identity_unique
Create Date: 2026-10-04 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261004_historical_signals_missing_indexes"
down_revision: str | None = "20261003_signal_identity_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_historical_signals_symbol ON historical_signals (symbol)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_historical_signals_timeframe ON historical_signals (timeframe)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_historical_signals_symbol")
    op.execute("DROP INDEX IF EXISTS ix_historical_signals_timeframe")
