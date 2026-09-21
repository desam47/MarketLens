"""
FastAPI dependencies for API routes
"""

from collections.abc import Generator

from sqlalchemy.orm import Session

from ..database import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """Dependency to get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
