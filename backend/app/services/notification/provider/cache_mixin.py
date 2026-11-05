"""Redis cache for notifications that have already been processed."""

import logging
from typing import TYPE_CHECKING

from redis import Redis as SyncRedis
from redis.exceptions import RedisError

from app.base_config import get_settings
from app.redis_config import create_sync_redis_client

if TYPE_CHECKING:
    from app.services.notification.model import Notification

logger = logging.getLogger("notification.provider.cache")

PROCESSED_NOTIFICATION_TTL_SECONDS = 240
PROCESSED_NOTIFICATION_VALUE = "processed"


class NotificationCacheMixin:
    """Cache processed notifications for four minutes."""

    _redis_client: SyncRedis | None = None

    def cache_notification(self, notification: "Notification") -> None:
        """Store a processed marker for the notification."""

        key = self._notification_cache_key(notification)
        try:
            self._get_redis().set(
                key,
                PROCESSED_NOTIFICATION_VALUE,
                ex=PROCESSED_NOTIFICATION_TTL_SECONDS,
            )
        except RedisError:
            logger.warning(
                "Failed to cache processed notification %s",
                notification.notification_id,
                exc_info=True,
            )

    def is_notification_cached(self, notification: "Notification") -> bool:
        """Return whether the notification already has a processed marker."""

        key = self._notification_cache_key(notification)
        try:
            return bool(self._get_redis().exists(key))
        except RedisError:
            logger.warning(
                "Failed to read notification cache for %s",
                notification.notification_id,
                exc_info=True,
            )
            return False

    def _get_redis(self) -> SyncRedis:
        """Return the sync Redis client, creating it on first use."""

        if self._redis_client is None:
            self._redis_client = create_sync_redis_client(get_settings())
        return self._redis_client

    def _notification_cache_key(self, notification: "Notification") -> str:
        """Build a cache key from the notification type and ID."""

        notification_type = getattr(
            notification.type,
            "value",
            notification.type,
        )
        return (
            f"notification:processed:{notification_type}:"
            f"{notification.notification_id}"
        )


__all__ = [
    "NotificationCacheMixin",
    "PROCESSED_NOTIFICATION_TTL_SECONDS",
    "PROCESSED_NOTIFICATION_VALUE",
]
