"""Tests for the PollerScheduler and periodic background tasks."""

from __future__ import annotations

import asyncio

import pytest

from resova.config.settings import Settings
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
