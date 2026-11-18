"""Redis pub/sub push notification provider."""

import json
import logging
from typing import TYPE_CHECKING

from redis import Redis as SyncRedis

from app.base_config import get_settings
from app.redis_config import create_sync_redis_client
from app.services.notification.exception import (
    ProviderError,
    ProviderServiceNotAvailableError,
)
from app.services.notification.provider.base import BaseProvider
from app.services.notification.provider.cache_mixin import NotificationCacheMixin

if TYPE_CHECKING:
    from app.services.notification.model import Notification

logger = logging.getLogger("notification.provider.push")


class PushProvider(NotificationCacheMixin, BaseProvider):
    """Publish push notifications to a Redis channel."""

    def __init__(
        self,
        redis_client: SyncRedis | None = None,
    ) -> None:
        settings = get_settings()
        self.channel = settings.notification_topic
        self._redis_client = redis_client

    def _get_publisher(self) -> SyncRedis:
        """Return the Redis client used for publishing."""

        if self._redis_client is None:
            self._redis_client = create_sync_redis_client(get_settings())
        return self._redis_client

    def process(self, notification: "Notification") -> None:
        """Publish the notification payload to the configured Redis channel."""

        if self.is_notification_cached(notification):
            logger.info(
                "Notification %s was already processed",
                notification.notification_id,
            )
            return

        try:
            payload = notification.payload
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise ProviderError(
                        "Push notification payload is invalid"
                    ) from exc
            if not isinstance(payload, dict):
                raise ProviderError("Push notification payload is invalid")
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("Push notification processing failed") from exc

        try:
            self._get_publisher().publish(
                self.channel,
                json.dumps(payload),
            )
        except Exception as exc:
            raise ProviderServiceNotAvailableError(
                "Push provider service is unavailable"
            ) from exc

        self.cache_notification(notification)
        logger.info(
            "Notification %s published to Redis channel %s",
            notification.notification_id,
            self.channel,
        )


__all__ = ["PushProvider"]
