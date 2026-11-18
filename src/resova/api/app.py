"""FastAPI application (Phase 1 / Step 2.8).

The API is deliberately tiny: **exactly one** endpoint, ``GET /health``, which reports the
application version and whether the configured database is reachable. There is no other
endpoint, and none is planned for Phase 1.

The server is a **local, single-user** tool and must be bound to the loopback interface
(``127.0.0.1``) - never to ``0.0.0.0``. Binding is done by the runner (``uvicorn`` via
``scripts/tasks.py run``), whose default host is ``127.0.0.1``.

Startup never requires a feature secret: the health check resolves only ``DATABASE_URL``
(see :func:`resova.config.settings.get_database_url`), so a fresh clone can run the API
before any credential exists.
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
from resova.config.settings import get_database_url
from resova.db.session import create_db_engine

logger = logging.getLogger(__name__)

#: The only host the API may ever be bound to.
ALLOWED_HOST = "127.0.0.1"


class HealthResponse(BaseModel):
    """Body of ``GET /health``. Contains no secret and no client data."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"]
    version: str
    database: Literal["ok", "unavailable"]


def _database_reachable(engine: Engine) -> bool:
    """Run a trivial ``SELECT 1``. Never raises; a failure only means *degraded*."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("health check: the database is not reachable")
        return False
    return True


def create_app() -> FastAPI:
    """Build the application.

    Exposed as a factory so tests can build an isolated instance; module level ``app``
    is what the ASGI server imports.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Creating an engine performs no I/O and needs no credential, so the app starts
        # even when nothing has been configured yet.
        app.state.engine = create_db_engine(get_database_url())
        try:
            yield
        finally:
            app.state.engine.dispose()

    app = FastAPI(
        title="Resova",
        version=__version__,
        # Documentation routes are disabled on purpose: the API exposes exactly one
        # endpoint and must not grow any other surface.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
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
        )

    return app


app = create_app()
