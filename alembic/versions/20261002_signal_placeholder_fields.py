"""clear placeholder volume_state / data_quality on historical signals

Every stored signal carried ``volume_state="normal"`` and ``data_quality="good"``:
the recorder wrote those constants without measuring volume or data quality, so
they read like evidence without being any. The recorder now leaves both empty;
this clears the constants already stored (HS-09).

Revision ID: 20261002_signal_placeholder_fields
Revises: 20261001_ai_trade_plan_provenance
Create Date: 2026-10-02 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261002_signal_placeholder_fields"
down_revision: str | None = "20261001_ai_trade_plan_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE historical_signals SET volume_state = NULL WHERE volume_state = 'normal'")
    op.execute("UPDATE historical_signals SET data_quality = NULL WHERE data_quality = 'good'")


def downgrade() -> None:
    # The old recorder stamped these constants on every row, so restoring them
    # everywhere reproduces the pre-migration state.
    op.execute("UPDATE historical_signals SET volume_state = 'normal' WHERE volume_state IS NULL")
    op.execute("UPDATE historical_signals SET data_quality = 'good' WHERE data_quality IS NULL")
