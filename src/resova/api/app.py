"""FastAPI application (Phase 1 / Step 2.8).

Exposes:
- ``GET /health``: reports service status, version, and database connectivity.
- Lifespan management: manages engine lifecycle and poller scheduler.

In production (``APP_ENV=production``) the API binds to ``0.0.0.0`` and disables
interactive docs. Locally it binds to ``127.0.0.1``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from resova import __version__
from resova.config.settings import Settings, get_settings
from resova.db.session import create_db_engine
from resova.scheduler.poller import PollerScheduler

logger = logging.getLogger(__name__)

#: Default local bind host (Phase 1 / Step 2.8).
ALLOWED_HOST = "127.0.0.1"


class HealthResponse(BaseModel):
    """Body of ``GET /health``. Contains no secret and no client data."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"]
    version: str
    database: Literal["ok", "unavailable"]
    environment: str


def _database_reachable(engine: Engine) -> bool:
    """Run a trivial ``SELECT 1``. Never raises; a failure only means *degraded*."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("health check: the database is not reachable")
        return False
    return True


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application factory."""

    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app_settings.app_env == "production":
            from resova.utils.logging_setup import configure_logging

            configure_logging(app_settings.log_level)

        app.state.engine = create_db_engine(app_settings.database_url)
        app.state.settings = app_settings

        # Start poller scheduler if enabled
        scheduler = PollerScheduler(app_settings, engine=app.state.engine)
        await scheduler.start()
        app.state.scheduler = scheduler

        logger.info(
            "Resova %s started (env=%s, db=%s)",
            __version__,
            app_settings.app_env,
            "postgresql" if "postgresql" in app_settings.database_url else "sqlite",
        )

        try:
            yield
        finally:
            if hasattr(app.state, "scheduler"):
                await app.state.scheduler.stop()
            app.state.engine.dispose()
            logger.info("Resova shutdown complete.")

    is_production = app_settings.app_env == "production"

    app = FastAPI(
        title="Resova",
        version=__version__,
        docs_url=None if is_production else "/docs",
        redoc_url=None,
        openapi_url=None if is_production else "/openapi.json",
        lifespan=lifespan,
    )

    @app.get("/health", response_model=HealthResponse, tags=["health"])
    def health(request: Request, response: Response) -> HealthResponse:
        """Report the application version and database connectivity."""
        database_ok = _database_reachable(request.app.state.engine)
        if not database_ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="ok" if database_ok else "degraded",
            version=__version__,
            database="ok" if database_ok else "unavailable",
            environment=app_settings.app_env,
        )

    return app


app = create_app()
