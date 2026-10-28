"""FastAPI application for the CourseWave student service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlmodel import SQLModel

from app.base_config import Settings, get_settings
from app.db_config import create_postgresql_engine
from app.logging_config import configure_logging
from app.middleware.check_proxy_signature import CheckProxySignatureMiddleware
from app.middleware.request_logger import RequestLoggerMiddleware
from app.redis_config import create_redis_client
from app.services.student.model import Outbox, Student  # noqa: F401
from app.services.student.router.student import router as student_router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""

    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Initialize and release application resources."""

        try:
            if settings.auto_create_tables:
                async with create_postgresql_engine().begin() as connection:
                    await connection.run_sync(SQLModel.metadata.create_all)

            yield
        finally:
            await app.state.redis.aclose()
            await create_postgresql_engine().dispose()
            create_postgresql_engine.cache_clear()

    app = FastAPI(
        title="CourseWave Student Service",
        description="Student service protected by API gateway request signing.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.redis = create_redis_client(settings)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(student_router)

    # The logger is added last so it wraps the signature middleware and also
    # records requests rejected with a 403.
    app.add_middleware(CheckProxySignatureMiddleware, settings=settings)
    app.add_middleware(RequestLoggerMiddleware)

    return app


app = create_app()
