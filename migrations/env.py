"""Async Alembic entry point; credentials come only from application settings."""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from nexusguard.core.config import get_settings
from nexusguard.infrastructure import models  # noqa: F401
from nexusguard.infrastructure.database import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=str(get_settings().database_url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def apply_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(str(get_settings().database_url), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(apply_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
elif connection := context.config.attributes.get("connection"):
    apply_migrations(connection)
else:
    asyncio.run(run_migrations_online())
