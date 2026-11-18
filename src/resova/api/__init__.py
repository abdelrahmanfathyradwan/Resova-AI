"""Minimal local HTTP API (Phase 1 / Step 2.8).

A FastAPI application exposing a single ``GET /health`` endpoint (application version plus
database connectivity). It must be bound to ``127.0.0.1``; no other endpoint exists.
"""

from resova.api.app import ALLOWED_HOST, HealthResponse, create_app

__all__ = ["ALLOWED_HOST", "HealthResponse", "app", "create_app"]

#: Module-level ASGI application imported by ``uvicorn resova.api:app``.
app = create_app()
