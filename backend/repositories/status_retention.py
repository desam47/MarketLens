"""
Retention for the append-only quote / provider-status / market-status / backfill-job tables.

The ingestion loops insert into these on every tick and nothing ever deleted from them
(only ``bars`` had a retention prune). Windows come from ``settings.retention``.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import BackfillJob
from backend.models.market_data_sql import MarketStatusModel, ProviderStatusModel, QuoteModel

logger = logging.getLogger(__name__)


def _prune_table(
    db: Session, model, cutoff: datetime, chunk_size: int, column: str = "timestamp"
) -> int:
    """Delete rows of ``model`` whose ``column`` is older than ``cutoff`` in short transactions.

    One statement per chunk keeps SQLite's single write lock held briefly, so the ingestion
    and API writers are never blocked behind a large delete.
    """
    total = 0
    while True:
        ids = select(model.id).where(getattr(model, column) < cutoff).limit(chunk_size)
        deleted = db.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
        total += deleted
        if deleted < chunk_size:
            return total


def prune_status_tables(
    db: Session, chunk_size: int = 5000, now: datetime | None = None
) -> dict[str, int]:
    """Prune quotes / provider_status / market_status to their configured windows.

    Returns ``{table: rows_deleted}`` for tables that actually lost rows (empty on the common
    no-op case), like ``prune_bars_by_retention``.
    """
    from backend.config.settings import settings

    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be > 0, got {chunk_size}")
    now = now or datetime.now()
    retention = settings.retention
    deleted: dict[str, int] = {}
    for name, model, days, column in (
        ("quotes", QuoteModel, retention.quotes_days, "timestamp"),
        ("provider_status", ProviderStatusModel, retention.provider_status_days, "timestamp"),
        ("market_status", MarketStatusModel, retention.market_status_days, "timestamp"),
        ("backfill_jobs", BackfillJob, retention.backfill_jobs_days, "created_at"),
    ):
        count = _prune_table(db, model, now - timedelta(days=days), chunk_size, column)
        if count:
            deleted[name] = count
    return deleted
