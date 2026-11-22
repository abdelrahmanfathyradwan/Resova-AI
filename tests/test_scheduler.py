"""Tests for the PollerScheduler and periodic background tasks."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine

from resova.config.settings import Settings, normalize_database_url
from resova.scheduler.poller import PollerScheduler


@pytest.fixture()
def scheduler_settings_disabled() -> Settings:
    return Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=False,
        enable_listing_poller=False,
        listing_poller_ack=False,
    )


@pytest.fixture()
def scheduler_settings_enabled() -> Settings:
    return Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=True,
        enable_listing_poller=True,
        listing_poller_ack=True,
        mostaql_listing_urls=["https://mostaql.com/projects"],
        telegram_bot_token="123456:ABC-token",
        telegram_allowed_user_id=123,
        listing_poll_interval_seconds=60,
        listing_poll_jitter_seconds=5,
    )


def test_scheduler_disabled_does_not_start(scheduler_settings_disabled: Settings) -> None:
    async def _run():
        scheduler = PollerScheduler(scheduler_settings_disabled)
        await scheduler.start()
        assert scheduler.is_running is False
        await scheduler.stop()

    asyncio.run(_run())


def test_scheduler_starts_and_stops_cleanly(scheduler_settings_enabled: Settings) -> None:
    async def _run():
        scheduler = PollerScheduler(scheduler_settings_enabled)
        await scheduler.start()
        assert scheduler.is_running is True

        # Graceful stop
        await scheduler.stop()
        assert scheduler.is_running is False
        assert scheduler._task is None

    asyncio.run(_run())


def test_scheduler_sqlite_leader_lock() -> None:
    """SQLite dialect treats local scheduler as leader unconditionally."""
    engine = create_engine("sqlite:///:memory:")
    scheduler = PollerScheduler(
        Settings(
            _env_file=None,
            enable_email_ingestion=False,
            enable_ai_analysis=False,
            enable_telegram=False,
        ),
        engine=engine,
    )
    assert scheduler._try_acquire_leader_lock() is True


def test_scheduler_postgresql_leader_lock_acquired() -> None:
    """PostgreSQL dialect acquires leader lock when advisory lock returns True."""
    mock_engine = MagicMock()
    mock_engine.dialect.name = "postgresql"
    mock_conn = MagicMock()
    mock_conn.closed = False
    mock_conn.execute.return_value.scalar.return_value = True
    mock_engine.connect.return_value = mock_conn

    scheduler = PollerScheduler(
        Settings(
            _env_file=None,
            enable_email_ingestion=False,
            enable_ai_analysis=False,
            enable_telegram=False,
        ),
        engine=mock_engine,
    )
    assert scheduler._try_acquire_leader_lock() is True
    assert scheduler._lock_conn is mock_conn

    # Release
    scheduler._release_leader_lock()
    mock_conn.close.assert_called_once()


def test_scheduler_postgresql_standby_when_lock_not_acquired() -> None:
    """PostgreSQL dialect stands by when another replica holds the advisory lock."""
    mock_engine = MagicMock()
    mock_engine.dialect.name = "postgresql"
    mock_conn = MagicMock()
    mock_conn.closed = False
    mock_conn.execute.return_value.scalar.return_value = False
    mock_engine.connect.return_value = mock_conn

    scheduler = PollerScheduler(
        Settings(
            _env_file=None,
            enable_email_ingestion=False,
            enable_ai_analysis=False,
            enable_telegram=False,
        ),
        engine=mock_engine,
    )
    assert scheduler._try_acquire_leader_lock() is False
    # Connection should be closed on rejection
    mock_conn.close.assert_called_once()


def test_normalize_database_url_railway_compat() -> None:
    """Railway-style postgres:// URLs are converted to postgresql:// for SQLAlchemy."""
    raw = "postgres://user:secret@roundhouse.proxy.rlwy.net:12345/railway"
    expected = "postgresql://user:secret@roundhouse.proxy.rlwy.net:12345/railway"
    assert normalize_database_url(raw) == expected

    # Untouched if already postgresql or sqlite
    assert normalize_database_url(expected) == expected
    assert normalize_database_url("sqlite:///./resova.db") == "sqlite:///./resova.db"
