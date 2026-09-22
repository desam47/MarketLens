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
# Database URL — use the same hard-coded path the app uses.
# ---------------------------------------------------------------------------
# ``backend.config.settings.DatabaseSettings`` hard-codes the DB path to
# ``<project_root>/marketlens.db`` (computed from the settings file's location,
# not the process CWD). This prevents the "watchlist disappeared on restart
# from wrong dir" bug. We import from there instead of reading
# ``DATABASE_URL`` from the environment.
#
# The override mechanism (set ``DatabaseSettings._DB_URL_OVERRIDE`` before
# construction) is also honored — tests that need a temp DB can use it.
from backend.config.settings import settings as _app_settings
_database_url: str = _app_settings.database.url

# ---------------------------------------------------------------------------
# SQLAlchemy models — import all so ``Base.metadata`` is complete.
# ---------------------------------------------------------------------------
# Import order matters: ``backend.database`` must be imported before the models
# so that ``DeclarativeBase`` is in scope. Models that inherit from it will
# then register themselves with ``Base.metadata``.
from backend.database import Base

# Import the whole models package (not a hand-picked subset) so every table
# registers with ``Base.metadata``, and any model added to
# ``backend/models/__init__.py`` in the future is automatically included
# here too. A hand-picked list drifted out of sync with that package once
# already (see the schema_completeness migration this fixed): a subset here
# means ``target_metadata`` is missing tables that DO exist in the live DB,
# and the next `alembic revision --autogenerate` reads that as "these were
# dropped from the models" and generates `op.drop_table(...)` for each —
# real data loss if applied without catching it in the diff. ``backend.models``
# imports only ORM (``Base`` subclass) and plain Pydantic model classes;
# only the former register into ``Base.metadata``, so this is safe to import
# wholesale.
import backend.models  # noqa: F401

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
