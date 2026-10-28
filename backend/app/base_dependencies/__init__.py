"""Shared FastAPI dependencies for the student service."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db_config import create_postgresql_engine

__all__ = ["get_session"]


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a PostgreSQL database session for a request."""

    engine = create_postgresql_engine()
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
