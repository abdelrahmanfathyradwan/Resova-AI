"""Background long-polling service for Telegram bot updates (Phase 5).

Polls Telegram for incoming messages and callback queries using getUpdates,
dispatches them to TelegramUserManager within transactional database sessions,
and coordinates with PostgreSQL advisory locks to ensure a single active poller
across multi-replica deployments.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Engine

from resova.config.settings import Settings
from resova.db.session import create_session_factory
from resova.telegram.bot import TelegramNotifier
from resova.telegram.manager import TelegramUserManager

logger = logging.getLogger(__name__)

RESOVA_TELEGRAM_LOCK_ID = 84729104


class TelegramPoller:
    """Polls Telegram for incoming updates and dispatches them to TelegramUserManager."""

    def __init__(
        self,
        settings: Settings,
        engine: Engine | None = None,
        *,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._notifier = notifier or TelegramNotifier(settings)
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._offset = 0
        self._lock_conn: Any = None
        self._is_leader = False

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    @property
    def is_running(self) -> bool:
        return self._running

    def _acquire_leader_lock(self) -> bool:
        """Acquire PostgreSQL advisory lock so only one replica polls Telegram."""
        if self._engine is None or self._engine.dialect.name == "sqlite":
            return True

        if self._engine.dialect.name == "postgresql":
            try:
                if self._lock_conn is None or self._lock_conn.closed:
                    self._lock_conn = self._engine.connect()
                result = self._lock_conn.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": RESOVA_TELEGRAM_LOCK_ID},
                ).scalar()
                acquired = bool(result)
                if not acquired and self._lock_conn is not None:
                    self._lock_conn.close()
                    self._lock_conn = None
                return acquired
            except Exception as exc:
                logger.warning("TelegramPoller: advisory lock check failed: %s", exc)
                if self._lock_conn is not None:
                    with contextlib.suppress(Exception):
                        self._lock_conn.close()
                    self._lock_conn = None
                return False

        return True

    def _release_leader_lock(self) -> None:
        """Release PostgreSQL advisory lock."""
        if self._lock_conn is not None and not self._lock_conn.closed:
            try:
                if self._engine and self._engine.dialect.name == "postgresql":
                    self._lock_conn.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": RESOVA_TELEGRAM_LOCK_ID},
                    )
            except Exception as exc:
                logger.debug("TelegramPoller: unlock error on close: %s", exc)
            finally:
                with contextlib.suppress(Exception):
                    self._lock_conn.close()
                self._lock_conn = None
        self._is_leader = False

    async def start(self) -> None:
        """Start the background polling loop if Telegram is configured."""
        if not self._settings.enable_telegram or not self._settings.telegram_bot_token:
            logger.info("TelegramPoller: Telegram disabled or no bot token configured.")
            return

        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("TelegramPoller: background polling task started.")

    async def stop(self) -> None:
        """Stop the background polling loop and release resources."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._release_leader_lock()
        logger.info("TelegramPoller: background polling task stopped.")

    async def poll_once(self, client: httpx.AsyncClient | None = None) -> int:
        """Fetch and process one batch of updates from Telegram. Returns count processed."""
        if not self._settings.telegram_bot_token:
            return 0

        token = self._settings.telegram_bot_token.get_secret_value()
        url = f"https://api.telegram.org/bot{token}/getUpdates"

        own_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=30.0)
            own_client = True

        count = 0
        try:
            resp = await client.post(
                url,
                json={"offset": self._offset, "timeout": 15},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    updates = data.get("result", [])
                    for update in updates:
                        self._offset = max(self._offset, update["update_id"] + 1)
                        self._process_single_update(update)
                        count += 1
            elif resp.status_code == 409:
                logger.warning(
                    "TelegramPoller: 409 conflict (webhook active or another poller). "
                    "Backing off 10s..."
                )
                await asyncio.sleep(10.0)
            else:
                logger.warning("TelegramPoller: getUpdates returned HTTP %s", resp.status_code)
                await asyncio.sleep(3.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("TelegramPoller: error during poll: %s", exc)
            await asyncio.sleep(3.0)
        finally:
            if own_client:
                await client.aclose()

        return count

    def _process_single_update(self, update: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a single update to TelegramUserManager in a transactional DB session."""
        if self._engine is None:
            logger.warning("TelegramPoller: no engine configured, skipping update.")
            return {"status": "skipped", "reason": "no_engine"}

        session_factory = create_session_factory(self._engine)
        with session_factory() as session:
            try:
                manager = TelegramUserManager(
                    session, self._settings, notifier=self._notifier
                )
                outcome = manager.process_update(update)
                session.commit()
                logger.info(
                    "TelegramPoller: processed update %s -> %s",
                    update.get("update_id"),
                    outcome.get("status"),
                )
                return outcome
            except Exception as exc:
                session.rollback()
                logger.error(
                    "TelegramPoller: failed processing update %s: %s",
                    update.get("update_id"),
                    exc,
                )
                return {"status": "error", "error": str(exc)}

    async def _poll_loop(self) -> None:
        """Main long-polling loop with leader election."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            while self._running:
                if not self._is_leader:
                    if self._acquire_leader_lock():
                        self._is_leader = True
                        logger.info("TelegramPoller: acquired leader lock. Active poller.")
                    else:
                        await asyncio.sleep(15.0)
                        continue

                try:
                    await self.poll_once(client)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.warning("TelegramPoller loop exception: %s", exc)
                    await asyncio.sleep(3.0)
