"""
Database configuration and session management
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config.settings import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models.

    Using SQLAlchemy 2.0's typed DeclarativeBase instead of the deprecated
    `declarative_base()` factory function. All models in backend/models/
    inherit from this class.
    """

# Create engine
engine = create_engine(
    settings.database.url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database.url else {},
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
