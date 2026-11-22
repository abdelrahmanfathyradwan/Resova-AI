"""Lightweight poller scheduler using asyncio.

Runs listing pollers on a configurable interval with jitter. Each run is logged
to the ``sync_logs`` table. The scheduler integrates with FastAPI's lifespan.

Distributed safety:
- When running against PostgreSQL, uses a session-level PostgreSQL advisory lock
  (``pg_try_advisory_lock``). Only the replica that successfully acquires the lock
  becomes the active scheduler. Other replicas remain in standby mode. If the active
  replica terminates or restarts, PostgreSQL automatically releases the advisory
  lock, allowing another replica to claim leadership.
- When running against SQLite (local development), single-replica execution is assumed.
- The scheduler is **disabled by default** (``ENABLE_LISTING_POLLER=false``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from typing import Any

from sqlalchemy import Engine, text

from resova.config.settings import Settings

logger = logging.getLogger(__name__)

#: Stable 64-bit advisory lock key for Resova's poller scheduler.
RESOVA_SCHEDULER_LOCK_ID = 849201948201


class PollerScheduler:
    """Manages periodic listing polling as an asyncio background task.

    Usage with FastAPI lifespan::

        scheduler = PollerScheduler(settings, engine=engine)
        await scheduler.start()
        # ... app runs ...
        await scheduler.stop()
    """

    def __init__(self, settings: Settings, engine: Engine | None = None) -> None:
        self._settings = settings
        self._engine = engine
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._is_leader = False
        self._lock_conn: Any = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_leader(self) -> bool:
        """Whether this instance holds the distributed scheduler leader lock."""
        return self._is_leader

    def _get_engine(self) -> Engine | None:
        """Return the database engine, lazily initializing if needed."""
        if self._engine is not None:
            return self._engine
        try:
            from resova.config.settings import get_database_url
            from resova.db.session import create_db_engine

            self._engine = create_db_engine(get_database_url())
            return self._engine
        except Exception as exc:
            logger.warning("PollerScheduler: unable to initialize engine for locking: %s", exc)
            return None

    def _try_acquire_leader_lock(self) -> bool:
        """Attempt to acquire the distributed scheduler leader lock.

        Returns True if this instance is the active scheduler leader.
        """
        engine = self._get_engine()
        if engine is None or engine.dialect.name == "sqlite":
            return True

        if engine.dialect.name == "postgresql":
            try:
                if self._lock_conn is None or self._lock_conn.closed:
                    self._lock_conn = engine.connect()
                result = self._lock_conn.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": RESOVA_SCHEDULER_LOCK_ID},
                ).scalar()
                acquired = bool(result)
                if not acquired and self._lock_conn is not None:
                    self._lock_conn.close()
                    self._lock_conn = None
                return acquired
            except Exception as exc:
                logger.warning("PollerScheduler: advisory lock check failed: %s", exc)
                if self._lock_conn is not None:
                    with contextlib.suppress(Exception):
                        self._lock_conn.close()
                    self._lock_conn = None
                return False

        return True

    def _verify_leader_lock(self) -> bool:
        """Verify the lock connection is still healthy."""
        engine = self._get_engine()
        if engine is None or engine.dialect.name != "postgresql":
            return True
        if self._lock_conn is None or self._lock_conn.closed:
            return False
        try:
            self._lock_conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def _release_leader_lock(self) -> None:
        """Release the PostgreSQL advisory lock and close the connection."""
        if self._lock_conn is not None and not self._lock_conn.closed:
            try:
                if self._engine and self._engine.dialect.name == "postgresql":
                    self._lock_conn.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": RESOVA_SCHEDULER_LOCK_ID},
                    )
            except Exception as exc:
                logger.debug("PollerScheduler: unlock error on close: %s", exc)
            finally:
                with contextlib.suppress(Exception):
                    self._lock_conn.close()
                self._lock_conn = None
        self._is_leader = False

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
        self._release_leader_lock()
        logger.info("Poller scheduler stopped.")

    async def _poll_loop(self) -> None:
        """Main polling loop with distributed leader lock and jitter."""
        while self._running:
            if not self._is_leader:
                self._is_leader = self._try_acquire_leader_lock()
                if not self._is_leader:
                    logger.info(
                        "PollerScheduler: lock held by another replica. "
                        "Standing by (retry in %ds).",
                        self._settings.listing_poll_interval_seconds,
                    )
                    try:
                        await asyncio.sleep(self._settings.listing_poll_interval_seconds)
                    except asyncio.CancelledError:
                        break
                    continue
                logger.info(
                    "PollerScheduler: acquired distributed lock. Active leader on this replica."
                )

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

            # Verify lock is still active
            if not self._verify_leader_lock():
                logger.warning("PollerScheduler: lock connection lost. Dropping leadership.")
                self._release_leader_lock()
                continue

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
