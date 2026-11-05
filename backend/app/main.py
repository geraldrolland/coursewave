"""FastAPI application for the CourseWave service."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

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
from app.services.notification.model import Notification  # noqa: F401
from app.services.notification.pub_sub.kafka import KafkaConsumer
from app.services.notification.ws import router as notification_ws_router
from app.services.student.model import Student  # noqa: F401
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

        kafka_consumer: KafkaConsumer | None = None
        kafka_task: asyncio.Task[None] | None = None
        if settings.kafka_consumer_enabled:
            kafka_consumer = KafkaConsumer(
                topic=settings.notification_topic,
                bootstrap_servers=settings.kafka_bootstrap_servers,
                group_id=settings.kafka_consumer_group_id,
            )
            kafka_task = asyncio.create_task(kafka_consumer.run())

        try:
            if settings.auto_create_tables:
                async with create_postgresql_engine().begin() as connection:
                    await connection.run_sync(SQLModel.metadata.create_all)

            yield
        finally:
            if kafka_task is not None:
                kafka_task.cancel()
                with suppress(asyncio.CancelledError):
                    await kafka_task
            if kafka_consumer is not None:
                await kafka_consumer.aclose()

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
    app.include_router(notification_ws_router)

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
