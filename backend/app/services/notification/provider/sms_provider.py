"""Termii SMS notification provider."""

import json
import logging
from typing import TYPE_CHECKING

from redis import Redis as SyncRedis
from termii_client import Termii

from app.base_config import get_settings
from app.services.notification.exception import (
    ProviderError,
    ProviderServiceNotAvailableError,
)
from app.services.notification.provider.base import BaseProvider
from app.services.notification.provider.cache_mixin import NotificationCacheMixin

if TYPE_CHECKING:
    from app.services.notification.model import Notification

logger = logging.getLogger("notification.provider.sms")


class SMSProvider(NotificationCacheMixin, BaseProvider):
    """Deliver SMS notifications through Termii."""

    def __init__(
        self,
        client: Termii | None = None,
        redis_client: SyncRedis | None = None,
    ) -> None:
        settings = get_settings()
        self.sender_id = settings.termii_sender_id
        self.channel = settings.termii_channel
        self.message_type = settings.termii_message_type
        self._client = client or Termii(settings.termii_api_key)
        self._redis_client = redis_client

    def process(self, notification: "Notification") -> None:
        """Send the notification payload through the Termii messaging API."""

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
                        "SMS notification payload is invalid"
                    ) from exc
            if not isinstance(payload, dict):
                raise ProviderError("SMS notification payload is invalid")

            payload = dict(payload)
            payload.setdefault("from", self.sender_id)
            payload.setdefault("type", self.message_type)
            payload.setdefault("channel", self.channel)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("SMS notification processing failed") from exc

        try:
            response = self._client.messaging.send(payload)
        except Exception as exc:
            raise ProviderServiceNotAvailableError(
                "SMS provider service is unavailable"
            ) from exc

        if isinstance(response, dict):
            response_code = response.get("code")
            if response_code not in (None, "ok"):
                message = response.get("message", "SMS delivery failed")
                raise ProviderServiceNotAvailableError(str(message))

        self.cache_notification(notification)
        logger.info(
            "Notification %s sent through Termii",
            notification.notification_id,
        )


__all__ = ["SMSProvider"]
