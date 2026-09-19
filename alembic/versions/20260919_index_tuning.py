"""index tuning: drop redundant indexes

Revision ID: 20260919_index_tuning
Revises: 20260911_ai_trade_plan_outcomes
Create Date: 2026-09-19

Every index is paid for on every INSERT/UPDATE/DELETE, so an index that no
query can benefit from is pure write amplification and dead disk. On the live
DB the ``bars`` table is ~89 MB but its indexes ~150 MB, and several of them
were redundant.

Dropped — each is provably subsumed, so no query loses an access path:

  * ``ix_<table>_id`` — SQLite already indexes the INTEGER PRIMARY KEY (it
    *is* the rowid). ``index=True`` on a primary-key Column made SQLAlchemy
    create a second, useless copy.
  * ``ix_bars_symbol``, ``ix_quotes_symbol``, ``ix_tape_bars_symbol``,
    ``ix_market_status_symbol``, ``ix_provider_status_provider_name`` — each
    is the leading column of a composite index on the same table, which
    serves the same lookups (verified with EXPLAIN QUERY PLAN: the symbol
    DELETE on ``bars`` and the latest-quote lookup both switch to the
    composite).
  * ``ix_bars_timeframe`` — leading column of ``ix_bars_timeframe_timestamp``.

Deliberately NOT touched, after measuring:

  * ``historical_signals`` keeps its ``symbol``/``timeframe`` indexes (only its
    redundant ``id`` index goes). A ``(symbol, timeframe, timestamp)``
    composite was prototyped to replace them, but on real query shapes it
    showed no measurable speedup for a ~39 MB index, so it was not added.
  * ``ix_*_timestamp`` (retention purge / MIN-MAX), ``ix_bars_provider_symbol``,
    ``ix_bars_source_timeframe``, ``ix_quotes_provider_symbol`` and
    ``ix_historical_signals_outcome_computed`` — each backs a query somewhere;
    dropping those needs usage evidence, not just structure.

The earlier ``20260902_index_cleanup`` migration dropped a few of these too,
but the live DB still had them, so this one is idempotent (``IF EXISTS`` /
``IF NOT EXISTS``) and safe to apply to any state.

Fully reversible: ``downgrade()`` recreates every dropped index. Data is never
touched. Freed pages go to SQLite's free list and are reused by later writes;
the file only shrinks on VACUUM.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = '20260919_index_tuning'
down_revision: Union[str, None] = '20260911_ai_trade_plan_outcomes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (index name, table, column) — all single-column, all redundant.
_REDUNDANT: tuple[tuple[str, str, str], ...] = (
    ("ix_bars_id", "bars", "id"),
    ("ix_bars_symbol", "bars", "symbol"),
    ("ix_bars_timeframe", "bars", "timeframe"),
    ("ix_quotes_id", "quotes", "id"),
    ("ix_quotes_symbol", "quotes", "symbol"),
    ("ix_tape_bars_id", "tape_bars", "id"),
    ("ix_tape_bars_symbol", "tape_bars", "symbol"),
    ("ix_market_status_id", "market_status", "id"),
    ("ix_market_status_symbol", "market_status", "symbol"),
    ("ix_provider_status_id", "provider_status", "id"),
    ("ix_provider_status_provider_name", "provider_status", "provider_name"),
    ("ix_historical_signals_id", "historical_signals", "id"),
)


def upgrade() -> None:
    for name, _table, _column in _REDUNDANT:
        op.execute(f"DROP INDEX IF EXISTS {name}")


def downgrade() -> None:
    for name, table, column in _REDUNDANT:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})")
