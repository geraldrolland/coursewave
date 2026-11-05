"""Kafka push notification provider."""

import json
import logging
from typing import TYPE_CHECKING

from redis import Redis as SyncRedis

from app.base_config import get_settings
from app.services.notification.exception import (
    ProviderError,
    ProviderServiceNotAvailableError,
)
from app.services.notification.provider.base import BaseProvider
from app.services.notification.provider.cache_mixin import NotificationCacheMixin
from app.services.notification.pub_sub.kafka_producer import (
    KafkaProducer,
    kafka_producer,
)

if TYPE_CHECKING:
    from app.services.notification.model import Notification

logger = logging.getLogger("notification.provider.push")


class PushProvider(NotificationCacheMixin, BaseProvider):
    """Publish push notifications to the configured Kafka topic."""

    def __init__(
        self,
        producer: KafkaProducer | None = None,
        redis_client: SyncRedis | None = None,
    ) -> None:
        settings = get_settings()
        self.topic = settings.notification_topic
        self._producer = producer or kafka_producer
        self._redis_client = redis_client

    def process(self, notification: "Notification") -> None:
        """Publish the notification payload to Kafka."""

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
            self._producer.send(self.topic, payload)
        except Exception as exc:
            raise ProviderServiceNotAvailableError(
                "Push provider service is unavailable"
            ) from exc

        self.cache_notification(notification)
        logger.info(
            "Notification %s published to Kafka topic %s",
            notification.notification_id,
            self.topic,
        )


__all__ = ["PushProvider"]
