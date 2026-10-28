"""Tests for the PostgreSQL engine configuration."""

import asyncio

from sqlalchemy.ext.asyncio import AsyncEngine

from app.db_config import create_postgresql_engine


def test_postgresql_engine_uses_configured_connection() -> None:
    engine = create_postgresql_engine()

    assert isinstance(engine, AsyncEngine)
    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.url.username == "postgres"
    assert engine.url.password == "user123"
    assert engine.url.host == "localhost"
    assert engine.url.port == 5432
    assert engine.url.database == "coursewave_db"


def test_postgresql_engine_is_cached() -> None:
    assert create_postgresql_engine() is create_postgresql_engine()


def teardown_module() -> None:
    """Dispose of the engine after this module's tests finish."""

    asyncio.run(create_postgresql_engine().dispose())
    create_postgresql_engine.cache_clear()
