"""one historical signal per (symbol, timeframe, timestamp)

The recorder checked for an existing row before inserting, but nothing in the
database enforced it, so writers racing past that check together (the
recording loop, startup gap-fill, a manual request, the backfill worker
process) stored the same bar twice. The live database had 1,879 such pairs,
which bias counts, averages, win rates, exports and replay (HS-12).

Upgrade removes the extra rows, then adds a unique index. Of each duplicate
group it keeps the most complete row: the most forward outcomes filled in,
then one with a regime, then the lowest id. The number removed per timeframe
is logged. It also drops ``ix_historical_signals_symbol_timeframe_timestamp``
where present: a non-unique copy of the same columns that exists on some
databases outside the migration history, which the unique index replaces.

Downgrade drops the unique index. The removed duplicates are not restored.

Revision ID: 20261003_signal_identity_unique
Revises: 20261002_signal_placeholder_fields
Create Date: 2026-10-03 00:00:00.000000+00:00
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261003_signal_identity_unique"
down_revision: str | None = "20261002_signal_placeholder_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_UNIQUE_INDEX = "uq_historical_signals_symbol_timeframe_timestamp"
_OLD_COMPOSITE = "ix_historical_signals_symbol_timeframe_timestamp"

# Rank each duplicate group: most outcomes present, then a regime, then the oldest id.
_EXTRA_ROWS = """
SELECT id, timeframe FROM (
    SELECT id, timeframe, ROW_NUMBER() OVER (
        PARTITION BY symbol, timeframe, timestamp
        ORDER BY
            (CASE WHEN return_5b IS NULL THEN 1 ELSE 0 END
             + CASE WHEN return_10b IS NULL THEN 1 ELSE 0 END
             + CASE WHEN return_20b IS NULL THEN 1 ELSE 0 END
             + CASE WHEN mfe IS NULL THEN 1 ELSE 0 END
             + CASE WHEN mae IS NULL THEN 1 ELSE 0 END),
            CASE WHEN market_regime IS NULL THEN 1 ELSE 0 END,
            id
    ) AS rank_in_group
    FROM historical_signals
) ranked
WHERE rank_in_group > 1
"""


def upgrade() -> None:
    bind = op.get_bind()
    extra = bind.execute(sa.text(_EXTRA_ROWS)).fetchall()
    if extra:
        per_timeframe: dict[str, int] = {}
        for _, timeframe in extra:
            per_timeframe[timeframe] = per_timeframe.get(timeframe, 0) + 1
        logger.info("Removing %d duplicate historical signals: %s", len(extra), per_timeframe)
        ids = [row_id for row_id, _ in extra]
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            bind.execute(
                sa.text("DELETE FROM historical_signals WHERE id IN :ids").bindparams(
                    sa.bindparam("ids", expanding=True)
                ),
                {"ids": chunk},
            )
    op.execute(f"DROP INDEX IF EXISTS {_OLD_COMPOSITE}")
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_UNIQUE_INDEX} "
        "ON historical_signals (symbol, timeframe, timestamp)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_UNIQUE_INDEX}")
