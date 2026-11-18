"""Tests for the Redis pub/sub push notification provider."""

import json
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
    """Minimal synchronous Redis double for push publishing and cache tests."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.published: list[tuple[str, str]] = []
        self.fail_exists = False
        self.fail_set = False
        self.fail_publish = False

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

    def publish(self, channel: str, payload: str) -> int:
        if self.fail_publish:
            raise RedisError("Redis publish failed")
        self.published.append((channel, payload))
        return 1


def make_notification(payload: Any) -> Notification:
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
    redis = FakeSyncRedis()
    provider = PushProvider(redis_client=redis)
    payload = {"title": "Hello", "body": "Push body"}
    notification = make_notification(payload)
    cache_key = notification_cache_key(notification)

    provider.process(notification)

    assert redis.published == [
        (
            get_settings().notification_topic,
            json.dumps(payload),
        ),
    ]
    assert redis.values[cache_key] == PROCESSED_NOTIFICATION_VALUE
    assert redis.ttls[cache_key] == PROCESSED_NOTIFICATION_TTL_SECONDS


def test_push_provider_skips_cached_notification() -> None:
    redis = FakeSyncRedis()
    notification = make_notification({"title": "Hello"})
    redis.values[notification_cache_key(notification)] = (
        PROCESSED_NOTIFICATION_VALUE
    )
    provider = PushProvider(redis_client=redis)

    provider.process(notification)

    assert redis.published == []


def test_push_provider_wraps_redis_errors_and_does_not_cache() -> None:
    redis = FakeSyncRedis()
    redis.fail_publish = True
    provider = PushProvider(redis_client=redis)
    notification = make_notification({"title": "Hello"})

    with pytest.raises(ProviderServiceNotAvailableError) as error:
        provider.process(notification)

    assert isinstance(error.value.__cause__, RedisError)
    assert redis.values == {}


def test_push_provider_rejects_invalid_payload() -> None:
    redis = FakeSyncRedis()
    provider = PushProvider(redis_client=redis)
    notification = make_notification("not-json")

    with pytest.raises(ProviderError, match="payload is invalid"):
        provider.process(notification)

    assert redis.published == []


def test_push_provider_accepts_json_string_payload() -> None:
    redis = FakeSyncRedis()
    provider = PushProvider(redis_client=redis)
    payload = {"title": "Hello", "body": "Push body"}
    notification = make_notification(json.dumps(payload))

    provider.process(notification)

    assert redis.published == [
        (
            get_settings().notification_topic,
            json.dumps(payload),
        ),
    ]
