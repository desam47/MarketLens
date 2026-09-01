"""
Database configuration and session management
"""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from backend.config.settings import settings, _PROJECT_ROOT


class Base(DeclarativeBase):
    """Declarative base for all ORM models.

    Using SQLAlchemy 2.0's typed DeclarativeBase instead of the deprecated
    `declarative_base()` factory function. All models in backend/models/
    inherit from this class.
    """

# Safety check: SQLite DB must live under the project root.
# Fail immediately (not silently) if someone starts the server from the
# backend/ directory with a relative DATABASE_URL — this prevents the
# "empty DB shadowing populated DB" class of bug.
_db_url = settings.database.url
if _db_url.startswith("sqlite:///"):
    _db_path = Path(_db_url.replace("sqlite:///", ""))
    if not _db_path.is_absolute():
        raise RuntimeError(
            f"SQLite DB path is not absolute: {_db_url!r}. "
            f"Set DATABASE_URL to an absolute path in .env to prevent this."
        )
    _db_canonical = _db_path.resolve()
    _root_canonical = _PROJECT_ROOT.resolve()
    if not str(_db_canonical).startswith(str(_root_canonical)):
        raise RuntimeError(
            f"SQLite DB must be inside project root {_root_canonical}. "
            f"Currently configured as: {_db_canonical}. "
            f"Set DATABASE_URL to an absolute path inside the project root."
        )

# Create engine
engine = create_engine(
    _db_url,
    connect_args={"check_same_thread": False} if "sqlite" in _db_url else {},
    echo=settings.database.echo
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
