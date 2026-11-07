"""Tests for the notification model, Celery configuration, and task."""

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from celery.exceptions import Reject

from app.base_config import get_settings
from app.services.notification.exception import NotificationError
from app.services.notification.model import (
    Notification,
    NotificationAudience,
    NotificationStatus,
    NotificationType,
)
from app.services.notification.task import process_notification
from app.services.notification.task.process_notification import (
    NOTIFICATION_LOCK_TTL_SECONDS,
    ProviderManager,
    _process_notification,
)
from celery_app import celery_app, declare_queues
from tests.fakes import FakeRedis


class RetryCalled(Exception):
    """Raised by the fake Celery task when retry is requested."""

    def __init__(self, countdown: float | None, exc: Exception | None) -> None:
        super().__init__("retry requested")
        self.countdown = countdown
        self.exc = exc


class FakeTask:
    """Minimal bound Celery task double."""

    def __init__(
        self,
        retries: int = 0,
        max_retries: int = 5,
        headers: dict[str, Any] | None = None,
    ) -> None:
        self.request = SimpleNamespace(retries=retries, headers=headers or {})
        self.max_retries = max_retries
        self.retry_calls: list[tuple[float | None, Exception | None]] = []
        self.retry_headers: list[dict[str, Any]] = []

    def retry(
        self,
        exc: Exception | None = None,
        countdown: float | None = None,
        headers: dict[str, Any] | None = None,
        **_: Any,
    ) -> None:
        self.retry_calls.append((countdown, exc))
        self.retry_headers.append(dict(headers or {}))
        raise RetryCalled(countdown, exc)


class FakeSession:
    """Minimal async SQLModel session double."""

    def __init__(
        self,
        notification: Notification | None,
        commits: list[NotificationStatus],
    ) -> None:
        self.notification = notification
        self.commits = commits

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    async def get(
        self,
        model: type[Notification],
        notification_id: UUID,
    ) -> Notification | None:
        return self.notification

    async def commit(self) -> None:
        if self.notification is not None:
            self.commits.append(self.notification.status)


class FakeSessionFactory:
    """Build a fresh fake session for each task query."""

    def __init__(self, notification: Notification | None) -> None:
        self.notification = notification
        self.commits: list[NotificationStatus] = []

    def __call__(self) -> FakeSession:
        return FakeSession(self.notification, self.commits)


def make_notification(
    status: NotificationStatus = NotificationStatus.PENDING,
) -> Notification:
    """Build a notification with predictable defaults."""

    return Notification(
        type=NotificationType.EMAIL,
        payload={"subject": "Welcome"},
        belongs_to=NotificationAudience.STUDENT,
        user_id=uuid4(),
        status=status,
    )


def test_notification_model_defaults_and_enums() -> None:
    notification = Notification(
        type=NotificationType.EMAIL,
        payload={"subject": "Welcome"},
        belongs_to=NotificationAudience.STUDENT,
        user_id=uuid4(),
    )

    assert notification.notification_id is not None
    assert notification.status is NotificationStatus.PENDING
    assert not hasattr(notification, "send")
    assert Notification.__table__.c.status.nullable is False
    assert "release_after" not in Notification.__table__.columns
    assert [member.value for member in NotificationType] == [
        "email",
        "push",
        "sms",
    ]
    assert [member.value for member in NotificationAudience] == [
        "student",
        "instructor",
        "admin",
    ]
    assert [member.value for member in NotificationStatus] == [
        "pending",
        "processed",
        "failed",
    ]


def test_celery_task_options() -> None:
    assert process_notification.name == "notification.process_notification"
    assert process_notification.max_retries == 5
    assert process_notification.default_retry_delay == 10
    assert not hasattr(process_notification, "retry_backoff")
    assert not hasattr(process_notification, "retry_jitter")
    assert not hasattr(process_notification, "autoretry_for")
    assert process_notification.acks_late is True
    assert process_notification.reject_on_worker_lost is True
    assert process_notification.acks_on_failure_or_timeout is False
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.task_acks_on_failure_or_timeout is False
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_celery_queue_topology() -> None:
    queues = {queue.name: queue for queue in celery_app.conf.task_queues}

    assert set(queues) == {"task", "task.dlq", "task.retry"}
    assert queues["task"].exchange.name == "task"
    assert queues["task"].exchange.type == "topic"
    assert queues["task"].routing_key == "task"
    assert queues["task"].queue_arguments == {
        "x-queue-type": "quorum",
        "x-delivery-limit": 5,
        "x-dead-letter-exchange": "task.dlx",
        "x-dead-letter-routing-key": "task.dlq",
    }
    assert queues["task.dlq"].exchange.name == "task.dlx"
    assert queues["task.dlq"].exchange.type == "topic"
    assert queues["task.dlq"].routing_key == "task.dlq"
    assert queues["task.dlq"].queue_arguments == {
        "x-queue-type": "quorum",
        "x-delivery-limit": -1,
    }
    assert queues["task.retry"].exchange.name == "task.dlx"
    assert queues["task.retry"].exchange.type == "topic"
    assert queues["task.retry"].routing_key == "task.retry"
    assert queues["task.retry"].queue_arguments == {
        "x-queue-type": "quorum",
        "x-message-ttl": 300000,
        "x-dead-letter-exchange": "task",
        "x-dead-letter-routing-key": "task",
        "x-dead-letter-strategy": "at-least-once",
        "x-overflow": "reject-publish",
    }
    assert celery_app.conf.task_default_queue == "task"
    assert celery_app.conf.task_default_exchange_type == "topic"
    assert celery_app.conf.broker_native_delayed_delivery_queue_type == "quorum"
    assert celery_app.conf.task_routes["notification.process_notification"] == {
        "queue": "task",
        "routing_key": "task",
    }
    assert celery_app.conf.broker_url == get_settings().rabbitmq_url
    assert (
        "app.services.notification.task.process_notification"
        in celery_app.conf.imports
    )


def test_worker_declares_every_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declared: list[str] = []

    class FakeQueue:
        def __init__(self, name: str) -> None:
            self.name = name

        def __call__(self, channel: Any) -> "FakeQueue":
            return self

        def declare(self) -> None:
            declared.append(self.name)

    class FakeConnection:
        default_channel = object()

        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *_: Any) -> bool:
            return False

    monkeypatch.setattr(
        celery_app,
        "connection_for_write",
        lambda: FakeConnection(),
    )
    monkeypatch.setattr(
        celery_app,
        "amqp",
        SimpleNamespace(
            queues={
                "task.dlq": FakeQueue("task.dlq"),
                "task": FakeQueue("task"),
                "task.retry": FakeQueue("task.retry"),
            }
        ),
    )

    declare_queues()

    assert declared == ["task.dlq", "task", "task.retry"]


def test_process_notification_marks_processed(monkeypatch: pytest.MonkeyPatch) -> None:
    notification = make_notification()
    redis = FakeRedis()
    factory = FakeSessionFactory(notification)
    task = FakeTask()
    sent: list[UUID] = []
    provider_types: list[NotificationType] = []

    class FakeProvider:
        def process(self, notification: Notification) -> None:
            sent.append(notification.notification_id)
            assert redis.ttls[
                f"notification:process:{notification.notification_id}"
            ] == NOTIFICATION_LOCK_TTL_SECONDS
            assert f"notification:commit:{notification.notification_id}" not in redis.values

    def get_provider(provider_type: NotificationType) -> FakeProvider:
        provider_types.append(provider_type)
        return FakeProvider()

    monkeypatch.setattr(
        ProviderManager,
        "get_provider",
        get_provider,
    )

    asyncio.run(
        _process_notification(
            task,
            notification.notification_id,
            redis,
            factory,
        )
    )

    assert sent == [notification.notification_id]
    assert provider_types == [NotificationType.EMAIL]
    assert notification.status is NotificationStatus.PROCESSED
    assert factory.commits == [NotificationStatus.PROCESSED]
    assert f"notification:process:{notification.notification_id}" not in redis.values
    assert f"notification:commit:{notification.notification_id}" not in redis.values


@pytest.mark.parametrize(
    "status",
    [NotificationStatus.PROCESSED, NotificationStatus.FAILED],
)
def test_terminal_notification_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
    status: NotificationStatus,
) -> None:
    notification = make_notification(status)
    redis = FakeRedis()
    factory = FakeSessionFactory(notification)
    task = FakeTask()

    def get_provider(_: NotificationType) -> None:
        pytest.fail("Provider must not be resolved for a terminal notification")

    monkeypatch.setattr(
        ProviderManager,
        "get_provider",
        get_provider,
    )

    asyncio.run(
        _process_notification(
            task,
            notification.notification_id,
            redis,
            factory,
        )
    )

    assert notification.status is status
    assert factory.commits == []
    assert f"notification:process:{notification.notification_id}" not in redis.values
    assert f"notification:commit:{notification.notification_id}" not in redis.values


def test_process_lock_busy_retries_with_lock_ttl() -> None:
    notification = make_notification()
    redis = FakeRedis()
    redis.seed(f"notification:process:{notification.notification_id}", "other")
    factory = FakeSessionFactory(notification)
    task = FakeTask(
        headers={
            "x-retries": 2,
            "x-death": [{"queue": "task"}],
            "x-first-death-queue": "task",
            "eta": "2026-01-01T00:00:00+00:00",
            "expires": "2026-01-01T00:00:00+00:00",
        }
    )

    with pytest.raises(RetryCalled) as exc_info:
        asyncio.run(
            _process_notification(
                task,
                notification.notification_id,
                redis,
                factory,
            )
        )

    assert exc_info.value.countdown == NOTIFICATION_LOCK_TTL_SECONDS
    assert exc_info.value.exc is None
    assert task.retry_calls[0][0] == NOTIFICATION_LOCK_TTL_SECONDS
    assert task.retry_headers[0] == {"x-retries": 2}
    assert factory.commits == []


@pytest.mark.parametrize("retries", [0, 5])
def test_notification_error_rejects_with_requeue_for_dlq(
    monkeypatch: pytest.MonkeyPatch,
    retries: int,
) -> None:
    notification = make_notification()
    redis = FakeRedis()
    factory = FakeSessionFactory(notification)
    task = FakeTask(retries=retries)

    class FailingProvider:
        def process(self, notification: Notification) -> None:
            raise NotificationError("delivery failed")

    monkeypatch.setattr(
        ProviderManager,
        "get_provider",
        lambda _: FailingProvider(),
    )

    with pytest.raises(Reject) as exc_info:
        asyncio.run(
            _process_notification(
                task,
                notification.notification_id,
                redis,
                factory,
            )
        )

    assert exc_info.value.requeue is True
    assert isinstance(exc_info.value.__cause__, NotificationError)
    assert notification.status is NotificationStatus.PENDING
    assert factory.commits == []
    assert task.retry_calls == []
    assert f"notification:process:{notification.notification_id}" not in redis.values
    assert f"notification:commit:{notification.notification_id}" not in redis.values


def test_missing_notification_returns_without_retry() -> None:
    redis = FakeRedis()
    factory = FakeSessionFactory(None)
    task = FakeTask()

    asyncio.run(
        _process_notification(
            task,
            uuid4(),
            redis,
            factory,
        )
    )

    assert task.retry_calls == []
    assert not any(key.startswith("notification:process:") for key in redis.values)
