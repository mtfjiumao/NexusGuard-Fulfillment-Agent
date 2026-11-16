import pytest
from fastapi.testclient import TestClient

from nexusguard.core.config import Settings
from nexusguard.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/15",
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client
