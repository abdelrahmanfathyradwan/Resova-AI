"""Tests for Phase 1 / Step 2.4 - database models, session wiring and migrations."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from resova.db.base import Base
from resova.db.models import (
    Platform,
    Project,
    ProjectAnalysis,
    SourceKind,
    UserAction,
)
from resova.db.session import create_db_engine, create_session_factory, session_scope

ROOT = Path(__file__).resolve().parents[1]

#: Expected tables (+ Alembic's own ``alembic_version``).
EXPECTED_TABLES = {
    "projects",
    "source_messages",
    "project_analyses",
    "notifications",
    "user_actions",
    "proposal_drafts",
    "proposal_access_tokens",
    "auto_proposal_jobs",
    "sync_logs",
    "user_profile",
    "user_professional_profiles",
    "system_settings",
    "telegram_users",
}


def _alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _project(**overrides: object) -> Project:
    data: dict[str, object] = {
        "platform": Platform.mostaql,
        "source_kind": SourceKind.alert_email,
        "title": "Fire alarm shop drawings",
    }
    data.update(overrides)
    return Project(**data)  # type: ignore[arg-type]


# --- schema shape --------------------------------------------------------------


def test_schema_has_exactly_the_expected_tables(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES


def test_every_model_maps_to_one_of_the_expected_tables(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    for table in Base.metadata.tables:
        assert table in tables
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_sqlite_foreign_keys_pragma_is_on(session: Session) -> None:
    assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1


# --- projects columns ----------------------------------------------------------


def test_projects_has_the_new_optional_columns(engine: Engine) -> None:
    columns = {col["name"]: col for col in inspect(engine).get_columns("projects")}
    for name in ("category", "source_section", "comments_count", "offers_count"):
        assert name in columns, f"missing column {name}"
        assert columns[name]["nullable"] is True


def test_projects_description_and_budget_fields_are_nullable(engine: Engine) -> None:
    columns = {col["name"]: col for col in inspect(engine).get_columns("projects")}
    for name in (
        "description",
        "budget_min",
        "budget_max",
        "budget_currency",
        "budget_type",
        "normalized_url",
    ):
        assert columns[name]["nullable"] is True, f"{name} must be nullable"


def test_project_analyses_has_an_explicit_analyzed_at(engine: Engine) -> None:
    columns = {col["name"]: col for col in inspect(engine).get_columns("project_analyses")}
    assert "analyzed_at" in columns
    assert columns["analyzed_at"]["nullable"] is False


# --- portable dedup keys -------------------------------------------------------


def test_multiple_null_dedup_keys_never_collide(session: Session) -> None:
    # A manual paste with no URL, no external id and no usable description: every key NULL.
    session.add(_project(source_kind=SourceKind.manual_paste))
    session.add(_project(source_kind=SourceKind.manual_paste))
    session.commit()
    assert session.query(Project).count() == 2


def test_duplicate_url_key_is_rejected(session: Session) -> None:
    session.add(_project(url_key="a" * 64))
    session.commit()
    session.add(_project(url_key="a" * 64))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_duplicate_external_key_is_rejected(session: Session) -> None:
    session.add(_project(external_key="b" * 64))
    session.commit()
    session.add(_project(external_key="b" * 64))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_duplicate_content_key_is_rejected(session: Session) -> None:
    session.add(_project(content_key="c" * 64))
    session.commit()
    session.add(_project(content_key="c" * 64))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --- relationships / cascade ---------------------------------------------------


def test_deleting_a_project_cascades_to_children(session: Session) -> None:
    project = _project()
    project.analyses.append(ProjectAnalysis(score=80))
    project.actions.append(UserAction(action="save"))
    session.add(project)
    session.commit()

    session.delete(project)
    session.commit()

    assert session.query(ProjectAnalysis).count() == 0
    assert session.query(UserAction).count() == 0


def test_analysis_score_out_of_range_is_rejected(session: Session) -> None:
    project = _project()
    project.analyses.append(ProjectAnalysis(score=101))
    session.add(project)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --- persistence ---------------------------------------------------------------


def test_data_persists_across_an_engine_restart(engine: Engine, database_url: str) -> None:
    factory = create_session_factory(engine)
    with factory() as sess:
        sess.add(_project(external_id="mostaql-123", url_key="d" * 64))
        sess.commit()
    engine.dispose()  # simulate the process shutting down

    reopened = create_db_engine(database_url)
    try:
        with create_session_factory(reopened)() as sess:
            stored = sess.query(Project).one()
            assert stored.external_id == "mostaql-123"
            assert stored.url_key == "d" * 64
    finally:
        reopened.dispose()


# --- alembic migrations --------------------------------------------------------


def test_alembic_upgrade_head_creates_every_table(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(_alembic_config(url), "head")

    eng = create_db_engine(url)
    try:
        tables = set(inspect(eng).get_table_names())
    finally:
        eng.dispose()

    assert tables == EXPECTED_TABLES | {"alembic_version"}


def test_migration_schema_matches_the_orm_metadata(tmp_path: Path) -> None:
    migrated_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(_alembic_config(migrated_url), "head")

    metadata_url = f"sqlite:///{tmp_path / 'metadata.db'}"
    meta_engine = create_db_engine(metadata_url)
    Base.metadata.create_all(meta_engine)

    migrated_engine = create_db_engine(migrated_url)
    try:
        for table in sorted(EXPECTED_TABLES):
            migrated_cols = {c["name"] for c in inspect(migrated_engine).get_columns(table)}
            metadata_cols = {c["name"] for c in inspect(meta_engine).get_columns(table)}
            assert migrated_cols == metadata_cols, f"column drift in {table}"
    finally:
        migrated_engine.dispose()
        meta_engine.dispose()


def test_alembic_downgrade_to_base_removes_everything(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    config = _alembic_config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    eng = create_db_engine(url)
    try:
        remaining = set(inspect(eng).get_table_names())
    finally:
        eng.dispose()

    assert remaining <= {"alembic_version"}


def test_session_factory_is_reusable(engine: Engine) -> None:
    factory: sessionmaker[Session] = create_session_factory(engine)
    with factory() as first:
        first.add(_project())
        first.commit()
    with factory() as second:
        assert second.query(Project).count() == 1


# --- transactional scope -------------------------------------------------------


def test_session_scope_commits_on_success(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with session_scope(factory) as sess:
        sess.add(_project())
    with factory() as check:
        assert check.query(Project).count() == 1


def test_session_scope_rolls_back_on_error(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with pytest.raises(RuntimeError), session_scope(factory) as sess:
        sess.add(_project())
        raise RuntimeError("boom")
    with factory() as check:
        assert check.query(Project).count() == 0
