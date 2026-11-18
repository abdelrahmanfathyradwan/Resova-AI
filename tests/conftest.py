"""Shared pytest fixtures (Phase 1 / Step 2.4+).

Tests use a real, file-backed SQLite database under ``tmp_path`` (never the project's
``resova.db``). A file database - not ``:memory:`` - is deliberate: it lets the suite
prove that data survives an engine restart and that SQLite pragmas behave as configured.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from resova.db.base import Base
from resova.db.session import create_db_engine, create_session_factory

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def database_url(tmp_path: Path) -> str:
    """A throwaway file-backed SQLite URL inside the test's temporary directory."""
    return f"sqlite:///{tmp_path / 'resova-test.db'}"


@pytest.fixture()
def engine(database_url: str) -> Iterator[Engine]:
    """An engine with the full schema created from the ORM metadata."""
    eng = create_db_engine(database_url)
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    """A session bound to the test engine."""
    factory = create_session_factory(engine)
    with factory() as sess:
        yield sess
