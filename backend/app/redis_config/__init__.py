"""Redis client configuration."""

from redis import Redis as SyncRedis
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


def create_sync_redis_client(settings: Settings) -> SyncRedis:
    """Create a synchronous Redis client from application settings."""

    return SyncRedis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password or None,
        db=settings.redis_db,
        decode_responses=True,
    )


__all__ = ["create_redis_client", "create_sync_redis_client"]
