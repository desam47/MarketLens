"""
Alembic environment for MarketLens.

This script is invoked by every alembic command (``upgrade``, ``downgrade``,
``revision``, ``current``, ``history`` …). It sets up the SQLAlchemy engine
and populates ``target_metadata`` so autogenerate can compare the live DB
against the model definitions.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add the project root to sys.path so ``from backend.models import *`` works.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---------------------------------------------------------------------------
# Database URL — read from environment, matching the rest of the app.
# ---------------------------------------------------------------------------
# ``alembic.ini`` leaves ``sqlalchemy.url`` blank; we populate it here from
# ``DATABASE_URL`` so the same env-var that powers the FastAPI app also
# powers migrations.
#
# The fallback is the same default used by ``backend/config/settings.py`` so
# running alembic with no env-var still works against ``marketlens.db``.
_database_url: str | None = os.environ.get(
    "DATABASE_URL",
    "sqlite:////Users/dips/projects/MarketLens/marketlens.db",
)

# ---------------------------------------------------------------------------
# SQLAlchemy models — import all so ``Base.metadata`` is complete.
# ---------------------------------------------------------------------------
# Import order matters: ``backend.database`` must be imported before the models
# so that ``DeclarativeBase`` is in scope. Models that inherit from it will
# then register themselves with ``Base.metadata``.
from backend.database import Base

# Now import every model so they register with ``Base.metadata``.
# Whitespace/comma splitting is intentional to keep the list scannable.
from backend.models.alert import Alert, AlertTrigger                                       # noqa: F401
from backend.models.backtest import BacktestRun, BacktestTrade                             # noqa: F401
from backend.models.experiment import Experiment                                           # noqa: F401
from backend.models.market_data_sql import (                                               # noqa: F401
    BarModel, MarketStatusModel, ProviderStatusModel, QuoteModel                            # noqa: F401
)
from backend.models.signal import HistoricalSignal                                         # noqa: F401
from backend.models.watchlist import Watchlist, WatchlistSymbol                            # noqa: F401

# ``target_metadata`` is what autogenerate compares against the live DB.
target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Alembic Config object — Alembic reads ``alembic.ini`` automatically.
# ---------------------------------------------------------------------------
config = context.config

# Set the database URL from our env-var override.
config.set_main_option("sqlalchemy.url", _database_url)

# Alembic's INI-file logging config (suppresses noise unless VERBOSE).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode — generates SQL without a DB connection.

    This is useful for: ``alembic upgrade head --sql`` and for reviewing
    what a migration would do before applying it.
    """
    url = config.get_main_option("sqlalchemy.url")
    if url is None:
        raise RuntimeError("sqlalchemy.url is not set (check DATABASE_URL)")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # MarketLens only uses SQLite in dev; include_transaction=False is
        # correct for SQLite's implicit transactions.
        include_transaction=False,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode — applies them against a live DB.

    ``engine_from_config`` reads ``sqlalchemy.url`` from the Alembic config
    (which we populated above from ``DATABASE_URL``).
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
