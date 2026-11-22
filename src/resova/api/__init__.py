"""Minimal local HTTP API (Phase 1 / Step 2.8).

A FastAPI application exposing a single ``GET /health`` endpoint (application version plus
database connectivity). In development docs are available at ``/docs``.
"""

from resova.api.app import HealthResponse, create_app

__all__ = ["HealthResponse", "app", "create_app"]

#: Module-level ASGI application imported by ``uvicorn resova.api:app``.
app = create_app()
