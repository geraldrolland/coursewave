"""Async Redis client configuration for the student service."""

from redis.asyncio import Redis

from app.base_config import Settings


def create_redis_client(settings: Settings) -> Redis:
    """Create an async Redis client from application settings."""

    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password or None,
        db=settings.redis_db,
        decode_responses=True,
    )