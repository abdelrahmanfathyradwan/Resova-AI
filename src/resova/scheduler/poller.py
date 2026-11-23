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
        # Initial brief pause to let server finish booting
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            return

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

            try:
                await self._run_poll_cycle()
            except Exception:
                logger.exception("Poll cycle failed unexpectedly")

            if not self._running:
                break

            interval = self._settings.listing_poll_interval_seconds
            jitter = random.randint(0, self._settings.listing_poll_jitter_seconds)
            wait = interval + jitter

            logger.info("PollerScheduler: sleeping %ds until next poll cycle...", wait)

            try:
                await asyncio.sleep(wait)
            except asyncio.CancelledError:
                break

            # Verify lock is still active
            if not self._verify_leader_lock():
                logger.warning("PollerScheduler: lock connection lost. Dropping leadership.")
                self._release_leader_lock()
                continue

    async def _run_poll_cycle(self) -> None:
        """Execute one poll cycle across configured listing sources."""
        logger.info("PollerScheduler: starting listing poll cycle...")
        try:
            from resova.db.base import utcnow
            from resova.db.models import Platform, Project, SyncLog, SyncStatus
            from resova.db.session import create_session_factory
            from resova.services.ingestion import IngestionService
            from resova.services.proposal import ProposalService
            from resova.sources.listing_poller import ListingPoller

            engine = self._get_engine()
            if engine is None:
                logger.warning("PollerScheduler: no engine available for poll cycle")
                return

            session_factory = create_session_factory(engine)
            with session_factory() as session:
                start_time = utcnow()
                poller = ListingPoller(settings=self._settings, session=session)
                new_raw = poller.fetch_new()

                sync_log = SyncLog(
                    platform=Platform.mostaql,
                    status=SyncStatus.success,
                    started_at=start_time,
                    finished_at=utcnow(),
                    items_seen=len(poller._known_keys),
                    items_new=len(new_raw),
                    items_updated=0,
                )
                session.add(sync_log)

                if not new_raw:
                    logger.info("PollerScheduler: no new projects discovered in this cycle.")
                    session.commit()
                    return

                logger.info(
                    "PollerScheduler: discovered %d new project(s). Ingesting up to 5...",
                    len(new_raw),
                )
                ingestion = IngestionService(session, self._settings)
                proposal_svc = ProposalService(session, self._settings)

                for raw in new_raw[:5]:
                    upsert_data = poller.normalize(raw)
                    if upsert_data is None:
                        continue
                    res = ingestion.ingest_project(upsert_data)
                    logger.info(
                        "PollerScheduler: ingested '%s' -> outcome=%s, score=%s",
                        raw.title,
                        res.get("outcome"),
                        res.get("analysis", {}).get("score") if res.get("analysis") else None,
                    )

                    project_id = res.get("project_id")
                    if project_id and res.get("analysis"):
                        proj = session.query(Project).filter_by(id=project_id).first()
                        if proj:
                            draft = proposal_svc.generate_draft(proj, language="ar")
                            logger.info(
                                "PollerScheduler: proposal draft %d created for project %d",
                                draft.id,
                                proj.id,
                            )

                sync_log.finished_at = utcnow()
                session.commit()
                logger.info("PollerScheduler: poll cycle completed successfully.")
        except Exception as exc:
            logger.exception("PollerScheduler: poll cycle failed unexpectedly: %s", exc)
