"""Tests for the minimal API (Phase 1 / Step 2.8).

The API must expose exactly one endpoint, ``GET /health``, and must never be bound to
anything other than the loopback interface. A fresh clone with no configured secrets has
to be able to start it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from resova import __version__
from resova.api import ALLOWED_HOST, HealthResponse, app, create_app


def _client(monkeypatch: pytest.MonkeyPatch, database_url: str) -> TestClient:
    monkeypatch.setenv("DATABASE_URL", database_url)
    return TestClient(create_app())


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client whose database is a real, writable SQLite file."""
    with _client(monkeypatch, f"sqlite:///{(tmp_path / 'health.db').as_posix()}") as c:
        yield c


@pytest.fixture
def broken_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client whose database path cannot be opened (the parent directory is missing)."""
    missing = tmp_path / "does-not-exist" / "health.db"
    with _client(monkeypatch, f"sqlite:///{missing.as_posix()}") as c:
        yield c


class TestHealthEndpoint:
    def test_reports_ok_with_the_application_version(self, client: TestClient) -> None:
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "version": __version__,
            "database": "ok",
        }

    def test_body_contains_only_the_documented_fields(self, client: TestClient) -> None:
        assert set(client.get("/health").json()) == {"status", "version", "database"}

    def test_reports_degraded_and_503_when_the_database_is_unreachable(
        self, broken_client: TestClient
    ) -> None:
        response = broken_client.get("/health")

        assert response.status_code == 503
        assert response.json() == {
            "status": "degraded",
            "version": __version__,
            "database": "unavailable",
        }

    def test_leaks_no_secret_or_client_data(self, client: TestClient) -> None:
        body = client.get("/health").text.lower()
        for forbidden in ("password", "token", "api_key", "secret", "openai"):
            assert forbidden not in body

    def test_rejects_non_get_methods(self, client: TestClient) -> None:
        assert client.post("/health").status_code == 405


class TestSurface:
    def test_exposes_exactly_one_get_endpoint(self) -> None:
        routes = [route for route in create_app().routes if isinstance(route, APIRoute)]

        assert [route.path for route in routes] == ["/health"]
        assert routes[0].methods == {"GET"}

    def test_documentation_routes_are_disabled(self) -> None:
        paths = {route.path for route in create_app().routes}

        assert paths.isdisjoint({"/docs", "/redoc", "/openapi.json"})

    def test_module_level_asgi_app_is_configured(self) -> None:
        assert app.title == "Resova"
        assert app.version == __version__

    def test_health_response_model_forbids_extra_fields(self) -> None:
        assert HealthResponse.model_config["extra"] == "forbid"


class TestBinding:
    def test_only_loopback_is_permitted(self) -> None:
        assert ALLOWED_HOST == "127.0.0.1"

    def test_starts_without_any_feature_secret(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Nothing but DATABASE_URL is provided: no IMAP, OpenAI or Telegram credential.
        for variable in (
            "IMAP_HOST",
            "IMAP_USER",
            "IMAP_PASSWORD",
            "OPENAI_API_KEY",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_ALLOWED_USER_ID",
        ):
            monkeypatch.delenv(variable, raising=False)

        with _client(monkeypatch, f"sqlite:///{(tmp_path / 'clean.db').as_posix()}") as client:
            assert client.get("/health").status_code == 200
