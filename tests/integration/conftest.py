"""Real PostgreSQL tests; migrations run only against an empty, named test database."""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]


def validate_test_url(raw: str):
    url = make_url(raw)
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").startswith(
        "nexusguard_test_"
    ):
        raise pytest.UsageError(
            "Integration tests require postgresql+asyncpg and a dedicated "
            "database named nexusguard_test_*; no migrations were run."
        )
    return url


@pytest.fixture(scope="session")
def postgres_url():
    raw = os.getenv("NEXUSGUARD_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set NEXUSGUARD_TEST_DATABASE_URL for real PostgreSQL integration tests")
    url = validate_test_url(raw)

    async def table_names():
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        finally:
            await engine.dispose()

    existing = set(asyncio.run(table_names()))
    if existing - {"alembic_version"}:
        raise pytest.UsageError(
            "The integration database must be empty; existing application tables "
            "were left untouched."
        )
    env = dict(os.environ)
    env.update(
        {
            "NEXUSGUARD_ENVIRONMENT": "test",
            "NEXUSGUARD_DATABASE_URL": url.render_as_string(hide_password=False),
            "NEXUSGUARD_REDIS_URL": "redis://localhost:6379/15",
            "NEXUSGUARD_LLM_API_KEY": "",
        }
    )

    def migrate(*args):
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            # Database credentials may be present in arbitrary driver diagnostics.
            pytest.fail("Alembic integration command failed: " + " ".join(args), pytrace=False)

    try:
        migrate("upgrade", "head")
        migrate("check")
        migrate("downgrade", "base")
        assert set(asyncio.run(table_names())) <= {"alembic_version"}
        migrate("upgrade", "head")
        migrate("check")
        yield url
    finally:
        # The production downgrade refuses active/new Agent message statuses. Only this
        # dedicated test database is archived here, after assertions and before teardown.
        async def archive_test_messages():
            engine = create_async_engine(url, poolclass=NullPool)
            try:
                async with engine.begin() as connection:
                    tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
                    if "reception_messages" in tables:
                        await connection.execute(
                            text("UPDATE reception_messages SET status = 'superseded'")
                        )
            finally:
                await engine.dispose()

        asyncio.run(archive_test_messages())
        migrate("downgrade", "base")


@pytest.fixture
def db(postgres_url):
    def execute(scenario):
        async def run():
            engine = create_async_engine(postgres_url, poolclass=NullPool)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                return await scenario(factory)
            finally:
                await engine.dispose()

        return asyncio.run(run())

    return execute
