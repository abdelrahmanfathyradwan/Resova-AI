"""Scheduled jobs (Phase 3+).

Provides a lightweight scheduler that integrates with FastAPI lifespan to run
periodic polling tasks. Each poll run is recorded in the ``sync_logs`` table.

The scheduler is intentionally simple: it uses asyncio tasks rather than a
heavy dependency like APScheduler.
"""

from resova.scheduler.poller import PollerScheduler

__all__ = ["PollerScheduler"]
