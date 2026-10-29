"""FastAPI application for the CourseWave service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis
from sqlmodel import SQLModel
from starlette.middleware.cors import CORSMiddleware

from app.base_config import Settings, get_settings
from app.db_config import create_postgresql_engine
from app.logging_config import configure_logging
from app.middleware.auth import AuthMiddleware
from app.middleware.cache import CacheMiddleware
from app.middleware.cors import cors_middleware_kwargs
from app.middleware.ratelimit import RateLimitMiddleware
from app.middleware.refresh_token import RefreshTokenMiddleware
from app.middleware.request_logger import RequestLoggerMiddleware
from app.redis_config import create_redis_client
from app.services.student.model import Outbox, Student  # noqa: F401
from app.services.student.router.student import router as student_router


def create_app(
    settings: Settings | None = None,
    redis_client: Redis | None = None,
) -> FastAPI:
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
        title="CourseWave API",
        description=(
            "CourseWave service with JWT authentication, CORS, response "
            "caching, and rate limiting."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.redis = redis_client or create_redis_client(settings)

    def redis_provider() -> Redis:
        """Resolve the current Redis client for middleware."""

        return app.state.redis

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(student_router)

    # add_middleware is LIFO: the last added middleware runs first.
    # Effective flow: CORS -> RequestLogger -> Auth -> RateLimit ->
    # RefreshToken -> Cache -> routes.
    app.add_middleware(
        CacheMiddleware,
        settings=settings,
        redis_provider=redis_provider,
    )
    app.add_middleware(
        RefreshTokenMiddleware,
        settings=settings,
        redis_provider=redis_provider,
    )
    app.add_middleware(
        RateLimitMiddleware,
        settings=settings,
        redis_provider=redis_provider,
    )
    app.add_middleware(
        AuthMiddleware,
        settings=settings,
        redis_provider=redis_provider,
    )
    app.add_middleware(RequestLoggerMiddleware)
    app.add_middleware(
        CORSMiddleware,
        **cors_middleware_kwargs(settings),
    )

    return app


app = create_app()
