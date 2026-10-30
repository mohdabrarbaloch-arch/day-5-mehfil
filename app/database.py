"""Database engine and session management.

SQLite by default, Postgres when MEHFIL_DATABASE_URL says so. The only
SQLite-specific tuning is the WAL pragma, which matters a lot here: chat
writes are frequent and small, and the default rollback journal serialises
readers against writers.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    """Declarative base for all models."""


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        # check_same_thread=False is required because FastAPI runs sync
        # dependencies in a threadpool.
        return {"connect_args": {"check_same_thread": False}, "pool_pre_ping": True}
    return {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}


engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))


if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session that always closes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables. Called on startup.

    Alembic would be the right answer for a long-lived deployment; for a
    single-service app with a stable schema, create_all is honest and
    sufficient. The README says so rather than pretending otherwise.
    """
    from . import models  # noqa: F401  (import registers the models)

    Base.metadata.create_all(bind=engine)
