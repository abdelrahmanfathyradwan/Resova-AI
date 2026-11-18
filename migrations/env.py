"""Alembic environment (Phase 1 / Step 2.4).

The database URL comes from ``DATABASE_URL`` (environment or ``.env``) through
``resova.config.settings.get_database_url`` - Alembic never hardcodes it and never
requires the feature secrets that the full ``Settings`` object validates.
"""

from __future__ import annotations

import logging

from alembic import context
from sqlalchemy import engine_from_config, pool

from resova.config.settings import get_database_url
from resova.db import models  # noqa: F401  (import registers every table)
from resova.db.base import Base

# Alembic is run standalone (``tasks.py migrate``), so give it a minimal, visible logger.
logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

config = context.config

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_database_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no live DB connection)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
