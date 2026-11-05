"""Tests for the Kafka push notification provider."""

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
from app.services.notification.provider.cache_mixin import (
    PROCESSED_NOTIFICATION_TTL_SECONDS,
    PROCESSED_NOTIFICATION_VALUE,
    NotificationCacheMixin,
)
from app.services.notification.provider.push_provider import PushProvider


class FakeSyncRedis:
    """Minimal synchronous Redis double for push cache tests."""

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


class FakeKafkaProducer:
    """Capture payloads published to Kafka."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def send(self, topic: str, payload: dict[str, Any]) -> None:
        self.sent.append((topic, payload))


def make_notification(payload: dict[str, Any]) -> Notification:
    """Build a push notification with predictable defaults."""

    return Notification(
        type=NotificationType.PUSH,
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


def test_push_provider_inherits_cache_mixin() -> None:
    assert isinstance(PushProvider(), NotificationCacheMixin)


def test_push_provider_publishes_payload_and_caches_notification() -> None:
    producer = FakeKafkaProducer()
    redis = FakeSyncRedis()
    provider = PushProvider(producer=producer, redis_client=redis)
    payload = {"title": "Hello", "body": "Push body"}
    notification = make_notification(payload)
    cache_key = notification_cache_key(notification)

    provider.process(notification)

    assert producer.sent == [
        (get_settings().notification_topic, payload),
    ]
    assert redis.values[cache_key] == PROCESSED_NOTIFICATION_VALUE
    assert redis.ttls[cache_key] == PROCESSED_NOTIFICATION_TTL_SECONDS


def test_push_provider_skips_cached_notification() -> None:
    class UnexpectedProducer:
        def send(self, topic: str, payload: dict[str, Any]) -> None:
            pytest.fail("Kafka must not be called for a cached notification")

    redis = FakeSyncRedis()
    notification = make_notification({"title": "Hello"})
    redis.values[notification_cache_key(notification)] = (
        PROCESSED_NOTIFICATION_VALUE
    )
    provider = PushProvider(
        producer=UnexpectedProducer(),
        redis_client=redis,
    )

    provider.process(notification)


def test_push_provider_wraps_kafka_errors_and_does_not_cache() -> None:
    class FailingProducer:
        def send(self, topic: str, payload: dict[str, Any]) -> None:
            raise RuntimeError("Kafka is unavailable")

    redis = FakeSyncRedis()
    provider = PushProvider(producer=FailingProducer(), redis_client=redis)
    notification = make_notification({"title": "Hello"})

    with pytest.raises(ProviderServiceNotAvailableError) as error:
        provider.process(notification)

    assert isinstance(error.value.__cause__, RuntimeError)
    assert redis.values == {}


def test_push_provider_rejects_invalid_payload() -> None:
    producer = FakeKafkaProducer()
    provider = PushProvider(
        producer=producer,
        redis_client=FakeSyncRedis(),
    )
    notification = make_notification({"title": "Hello"})
    notification.payload = "not-json"

    with pytest.raises(ProviderError, match="payload is invalid"):
        provider.process(notification)

    assert producer.sent == []
