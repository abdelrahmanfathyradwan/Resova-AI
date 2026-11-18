"""Declarative base, naming conventions and shared column mixins (Step 2.4).

Portability rule: portable SQLAlchemy only. Constraint and index names are generated
from a deterministic naming convention so generated migrations are stable across
environments and across SQLite/PostgreSQL.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: Deterministic names for constraints/indexes so migrations stay stable.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Current UTC time as a naive datetime.

    Stored naive so the value is identical on SQLite and PostgreSQL (SQLite has no
    timezone type); callers must treat every stored timestamp as UTC.
    """
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Declarative base for every Resova ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Adds ``created_at`` / ``updated_at`` UTC timestamps to a model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )
