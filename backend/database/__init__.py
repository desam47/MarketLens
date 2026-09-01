# Re-export from db.py so existing `from backend.database import ...` calls
# keep working unchanged after the file was moved into a package.
from backend.database.db import Base, SessionLocal, engine, get_db

__all__ = ["Base", "SessionLocal", "engine", "get_db"]
