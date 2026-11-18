"""Engine and session wiring (Phase 1 / Step 2.4).

Portable SQLAlchemy only. SQLite is tuned (``foreign_keys=ON`` + WAL) but only through
a dialect-guarded connect hook, so switching ``DATABASE_URL`` to PostgreSQL is a
configuration change and nothing else.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from resova.db.base import Base


def create_db_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create an engine, applying SQLite tuning only when the dialect is SQLite."""
    engine = create_engine(database_url, echo=echo, future=True)

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
            # ``foreign_keys`` is OFF by default in SQLite; WAL improves concurrency.
            if isinstance(dbapi_connection, sqlite3.Connection):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory for ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def create_schema(engine: Engine) -> None:
    """Create every table from the ORM metadata.

    Used by tests only; production schema changes go through Alembic migrations.
    """
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any exception."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
