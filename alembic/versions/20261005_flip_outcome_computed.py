"""flip outcome_computed column semantics

The Python attribute ``_outcome_missing`` maps to the SQL column
``outcome_computed`` with inverted semantics: at INSERT the code set
``_outcome_missing=True`` which wrote ``outcome_computed=True`` to the DB,
meaning "outcomes are computed" for a row that had no outcomes yet.  After a
successful backfill it set ``_outcome_missing=False`` → ``outcome_computed=False``,
meaning "outcomes are NOT computed" for a fully-filled row.  The ``ix_historical_signals_outcome_computed``
index was therefore useless: any correct query for pending rows had to use a
slow 5-column OR-of-NULLs scan instead.

This migration flips every non-NULL value so the column carries the intended
semantics: ``TRUE`` = outcomes are present, ``FALSE`` = outcomes are pending.
The companion code rename (``_outcome_missing`` → ``outcome_computed``) and
the updated ``get_signals_needing_outcomes`` query land in the same deploy.

Revision ID: 20261005_flip_outcome_computed
Revises: 20261004_historical_signals_missing_indexes
Create Date: 2026-10-05 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261005_flip_outcome_computed"
down_revision: str | None = "20261004_historical_signals_missing_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Flip TRUE ↔ FALSE; NULL rows stay NULL (treated as pending by the new
    # query filter ``outcome_computed IS NOT TRUE``).
    op.execute(
        """
        UPDATE historical_signals
        SET outcome_computed = CASE
            WHEN outcome_computed = 1 THEN 0
            WHEN outcome_computed = 0 THEN 1
            ELSE NULL
        END
        """
    )


def downgrade() -> None:
    # Re-flip to restore the old (inverted) values.
    op.execute(
        """
        UPDATE historical_signals
        SET outcome_computed = CASE
            WHEN outcome_computed = 1 THEN 0
            WHEN outcome_computed = 0 THEN 1
            ELSE NULL
        END
        """
    )
