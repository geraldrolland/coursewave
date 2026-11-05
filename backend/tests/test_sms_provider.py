"""Tests for the Termii SMS notification provider."""

from typing import Any
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.base_config import get_settings
from app.services.notification.exception import (
    ProviderError,
    ProviderServiceNotAvailableError,
)
from app.services.notification.model import (
    Notification,
    NotificationAudience,
    NotificationType,
)
from app.services.notification.provider.base import BaseProvider
from app.services.notification.provider.cache_mixin import (
    PROCESSED_NOTIFICATION_TTL_SECONDS,
    PROCESSED_NOTIFICATION_VALUE,
    NotificationCacheMixin,
)
from app.services.notification.provider.sms_provider import SMSProvider


class FakeSyncRedis:
    """Minimal synchronous Redis double for SMS cache tests."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.fail_exists = False
        self.fail_set = False

    def exists(self, key: str) -> int:
        if self.fail_exists:
            raise RedisError("Redis read failed")
        return int(key in self.values)

    def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
    ) -> bool:
        if self.fail_set:
            raise RedisError("Redis write failed")
        self.values[key] = value
        self.ttls[key] = ex
        return True


class FakeTermiiMessaging:
    """Capture SMS payloads sent through the Termii SDK."""

    def __init__(
        self,
        response: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or {"code": "ok", "message": "Successfully Sent"}
        self.error = error
        self.payloads: list[dict[str, Any]] = []

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        self.payloads.append(payload)
        return self.response


class FakeTermii:
    """Minimal Termii client double."""

    def __init__(
        self,
        response: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.messaging = FakeTermiiMessaging(response=response, error=error)


def make_notification(payload: dict[str, Any]) -> Notification:
    """Build an SMS notification with predictable defaults."""

    return Notification(
        type=NotificationType.SMS,
        payload=payload,
        belongs_to=NotificationAudience.STUDENT,
        user_id=uuid4(),
    )


def notification_cache_key(notification: Notification) -> str:
    """Return the expected processed-notification cache key."""

    return (
        f"notification:processed:{notification.type.value}:"
        f"{notification.notification_id}"
    )


def test_sms_provider_inherits_base_provider_and_cache_mixin() -> None:
    provider = SMSProvider(client=FakeTermii(), redis_client=FakeSyncRedis())

    assert isinstance(provider, BaseProvider)
    assert isinstance(provider, NotificationCacheMixin)


def test_sms_provider_sends_payload_with_defaults() -> None:
    client = FakeTermii()
    redis = FakeSyncRedis()
    provider = SMSProvider(client=client, redis_client=redis)
    payload = {"to": "2348012345678", "sms": "Hello from CourseWave"}
    notification = make_notification(payload)
    settings = get_settings()
    cache_key = notification_cache_key(notification)

    provider.process(notification)

    assert len(client.messaging.payloads) == 1
    sent_payload = client.messaging.payloads[0]
    assert sent_payload["to"] == payload["to"]
    assert sent_payload["sms"] == payload["sms"]
    assert sent_payload["from"] == settings.termii_sender_id
    assert sent_payload["type"] == settings.termii_message_type
    assert sent_payload["channel"] == settings.termii_channel
    assert "from" not in payload
    assert "type" not in payload
    assert "channel" not in payload
    assert redis.values[cache_key] == PROCESSED_NOTIFICATION_VALUE
    assert redis.ttls[cache_key] == PROCESSED_NOTIFICATION_TTL_SECONDS


def test_sms_provider_accepts_json_payload() -> None:
    client = FakeTermii()
    provider = SMSProvider(
        client=client,
        redis_client=FakeSyncRedis(),
    )
    notification = make_notification({"to": "2348012345678", "sms": "Hello"})
    notification.payload = '{"to": "2348012345678", "sms": "Hello"}'

    provider.process(notification)

    assert client.messaging.payloads[0]["sms"] == "Hello"


def test_sms_provider_skips_cached_notification() -> None:
    redis = FakeSyncRedis()
    client = FakeTermii()
    provider = SMSProvider(client=client, redis_client=redis)
    notification = make_notification(
        {"to": "2348012345678", "sms": "Hello"}
    )
    redis.values[notification_cache_key(notification)] = (
        PROCESSED_NOTIFICATION_VALUE
    )

    provider.process(notification)

    assert client.messaging.payloads == []


def test_sms_provider_wraps_termii_errors() -> None:
    client = FakeTermii(error=RuntimeError("Termii is unavailable"))
    redis = FakeSyncRedis()
    provider = SMSProvider(client=client, redis_client=redis)
    notification = make_notification(
        {"to": "2348012345678", "sms": "Hello"}
    )

    with pytest.raises(ProviderServiceNotAvailableError) as error:
        provider.process(notification)

    assert isinstance(error.value.__cause__, RuntimeError)
    assert redis.values == {}


def test_sms_provider_rejects_termii_error_response() -> None:
    client = FakeTermii(
        response={"code": "error", "message": "Invalid sender ID"}
    )
    redis = FakeSyncRedis()
    provider = SMSProvider(client=client, redis_client=redis)
    notification = make_notification(
        {"to": "2348012345678", "sms": "Hello"}
    )

    with pytest.raises(ProviderServiceNotAvailableError, match="Invalid sender ID"):
        provider.process(notification)

    assert redis.values == {}


def test_sms_provider_rejects_invalid_payload() -> None:
    client = FakeTermii()
    redis = FakeSyncRedis()
    provider = SMSProvider(client=client, redis_client=redis)
    notification = make_notification({"to": "2348012345678", "sms": "Hello"})
    notification.payload = "not-json"

    with pytest.raises(ProviderError, match="payload is invalid"):
        provider.process(notification)

    assert client.messaging.payloads == []
    assert redis.values == {}
