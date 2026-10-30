"""Database engine and session management.

SQLite by default, Postgres when MEHFIL_DATABASE_URL says so. The only
SQLite-specific concessions are check_same_thread=False (FastAPI runs sync
dependencies on a threadpool) and WAL mode, so a read does not block a write
while a reply is streaming.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base for all models."""


_is_sqlite = settings.database_url.startswith("sqlite")

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,
    future=True,
)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):  # noqa: ANN001
        """WAL for concurrency, and foreign keys, which SQLite disables by default."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session that always closes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables. Called on startup.

    create_all is fine here: the schema is small and there is no migration
    history to preserve yet. A real deployment would move to Alembic before
    the first schema change.
    """
    from app import models  # noqa: F401  (import registers the models)

    Base.metadata.create_all(bind=engine)
