"""Create and initialize the configured PostgreSQL database.

Run this command before starting the API or worker. It is deliberately separate
from the API lifespan so that regular service restarts never apply migrations.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from .config import get_settings
from .models import Base


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAINTENANCE_DATABASE = "postgres"


def postgres_url(database_url: str) -> tuple[str, str]:
    """Return a maintenance connection URL and the configured database name."""
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql" or url.get_driver_name() != "asyncpg":
        raise ValueError("DATABASE_URL must use the postgresql+asyncpg driver")
    if not url.database:
        raise ValueError("DATABASE_URL must include a database name")
    return url.set(database=MAINTENANCE_DATABASE).render_as_string(hide_password=False), url.database


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


async def ensure_database_exists(database_url: str) -> bool:
    """Create the configured database when the connected role has permission."""
    maintenance_url, database_name = postgres_url(database_url)
    engine = create_async_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            exists = await connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = :database_name)"),
                {"database_name": database_name},
            )
            if exists:
                return False
            await connection.execute(text(f"CREATE DATABASE {quote_identifier(database_name)}"))
            return True
    finally:
        await engine.dispose()


async def has_application_tables(database_url: str) -> bool:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_connection: bool(
                    set(inspect(sync_connection).get_table_names()) & set(Base.metadata.tables)
                )
            )
    finally:
        await engine.dispose()


async def create_current_schema(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


def run_alembic(*arguments: str, database_url: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
    )


def main() -> None:
    database_url = get_settings().database_url
    created = asyncio.run(ensure_database_exists(database_url))
    if created or not asyncio.run(has_application_tables(database_url)):
        asyncio.run(create_current_schema(database_url))
        run_alembic("stamp", "head", database_url=database_url)
        print("Database schema created and marked at the current Alembic head.")
        return

    run_alembic("upgrade", "head", database_url=database_url)
    print("Existing database upgraded to the current Alembic head.")


if __name__ == "__main__":
    main()
