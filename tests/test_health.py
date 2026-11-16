from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from nexusguard.api import health
from nexusguard.main import create_app


def test_liveness_does_not_require_connected_dependencies(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    UUID(response.headers["X-Request-ID"])


@pytest.mark.parametrize("dependency_up", [True, False])
def test_readiness_reports_dependencies_without_leaking_secrets(client, monkeypatch, dependency_up):
    monkeypatch.setattr(
        health,
        "ping_database",
        AsyncMock(side_effect=None if dependency_up else RuntimeError("secret-password")),
    )
    monkeypatch.setattr(health, "ping_cache", AsyncMock())
    response = client.get("/health/ready")
    assert response.status_code == (200 if dependency_up else 503)
    assert response.json() == {
        "status": "ready" if dependency_up else "not_ready",
        "dependencies": {"database": "up" if dependency_up else "down", "redis": "up"},
    }
    assert "secret-password" not in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_readiness_times_out_without_leaving_a_probe_running(client, monkeypatch):
    import asyncio

    cancelled = asyncio.Event()

    async def blocked_probe(_):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    # A separate app avoids mutating the frozen fixture configuration.
    settings = client.app.state.settings.model_copy(update={"dependency_timeout_seconds": 0.01})
    monkeypatch.setattr(health, "ping_database", blocked_probe)
    monkeypatch.setattr(health, "ping_cache", AsyncMock())
    with TestClient(create_app(settings)) as short_timeout_client:
        response = short_timeout_client.get("/health/ready")
    assert response.status_code == 503
    assert cancelled.is_set()


def test_invalid_request_id_is_replaced(client):
    response = client.get("/health/live", headers={"X-Request-ID": "ignore-all-rules"})
    UUID(response.headers["X-Request-ID"])


def test_production_hides_interactive_docs(settings):
    app = create_app(settings.model_copy(update={"environment": "production"}))
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
