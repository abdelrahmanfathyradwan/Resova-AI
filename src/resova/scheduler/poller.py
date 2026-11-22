"""Lightweight poller scheduler using asyncio.

Runs listing pollers on a configurable interval with jitter. Each run is logged
to the ``sync_logs`` table. The scheduler integrates with FastAPI's lifespan.

The scheduler is **disabled by default** (``ENABLE_LISTING_POLLER=false``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random

from resova.config.settings import Settings

logger = logging.getLogger(__name__)


class PollerScheduler:
    """Manages periodic listing polling as an asyncio background task.

    Usage with FastAPI lifespan::

        scheduler = PollerScheduler(settings)
        await scheduler.start()
        # ... app runs ...
        await scheduler.stop()
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start the polling loop if the listing poller is enabled."""
        if not self._settings.enable_listing_poller:
            logger.info("Listing poller is disabled; scheduler not started.")
            return

        if not self._settings.listing_poller_ack:
            logger.warning(
                "LISTING_POLLER_ACK is false; refusing to start the poller. "
                "Set LISTING_POLLER_ACK=true to acknowledge the listing poller."
            )
            return

        if self._running:
            logger.warning("Poller scheduler is already running.")
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Poller scheduler started.")

    async def stop(self) -> None:
        """Stop the polling loop gracefully."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("Poller scheduler stopped.")

    async def _poll_loop(self) -> None:
        """Main polling loop with jitter."""
        while self._running:
            interval = self._settings.listing_poll_interval_seconds
            jitter = random.randint(0, self._settings.listing_poll_jitter_seconds)
            wait = interval + jitter

            logger.debug("Next poll in %d seconds (interval=%d, jitter=%d)", wait, interval, jitter)

            try:
                await asyncio.sleep(wait)
            except asyncio.CancelledError:
                break

            if not self._running:
                break

            try:
                await self._run_poll_cycle()
            except Exception:
                logger.exception("Poll cycle failed unexpectedly")

    async def _run_poll_cycle(self) -> None:
        """Execute one poll cycle.

        This method is intentionally a no-op skeleton that logs the cycle.
        The actual HTTP fetching is handled by ListingPoller which is NOT
        enabled by default. This prevents any unauthorized network requests.

        When the listing poller is properly configured and authorized, this
        method would:
        1. Instantiate the ListingPoller
        2. Call fetch_new() to get new projects
        3. Normalize and ingest each project
        4. Record the sync log
        """
        logger.info(
            "Poll cycle triggered. Listing poller is configured but actual HTTP "
            "requests require explicit setup of source connectors. "
            "See docs/SOURCES.md for the authorized discovery channels."
        )
