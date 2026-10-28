"""PostgreSQL engine configuration for the student service."""

import os
from functools import lru_cache

from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

__all__ = ["create_postgresql_engine"]

POSTGRES_URL = URL.create(
    drivername="postgresql+asyncpg",
    username="postgres",
    password="user123",
    host="localhost",
    port=5432,
    database="coursewave_db",
)

DATABASE_URL = os.getenv("DATABASE_URL")


@lru_cache(maxsize=1)
def create_postgresql_engine() -> AsyncEngine:
    """Create and cache the PostgreSQL async engine."""

    return create_async_engine(
        DATABASE_URL or POSTGRES_URL,
        pool_pre_ping=True,
    )
