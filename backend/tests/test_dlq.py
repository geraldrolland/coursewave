"""Tests for the task DLQ consumer and terminal notification handler."""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.services.notification.model import (
    Notification,
    NotificationAudience,
    NotificationStatus,
    NotificationType,
)
from dlq import consumer
from dlq.consumer import (
    DLQ_RETRY_THROTTLE_SECONDS,
    HandlerCall,
    MalformedMessage,
    TaskCall,
    build_retry_headers,
    handle_decode_error,
    handle_message,
    invoke_handler,
    parse_task_call,
    read_retry_count,
    resolve_handler,
)
from dlq.handlers import (
    HANDLERS,
    InvalidNotificationId,
    _mark_notif_failed,
    mark_notif_failed,
)

TASK_NAME = "notification.process_notification"


class FakeMessage:
    """Minimal Kombu message double."""

    def __init__(
        self,
        payload: Any,
        headers: dict[str, Any] | None = None,
    ) -> None:
        self.payload = payload
        self.headers = headers if headers is not None else {}
        self.acked = False
        self.rejections: list[bool] = []

    def ack(self) -> None:
        self.acked = True

    def reject(self, requeue: bool = False) -> None:
        self.rejections.append(requeue)


class FakeProducer:
    """Record published retry messages."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.published: list[tuple[Any, dict[str, Any]]] = []

    def publish(self, body: Any, **options: Any) -> None:
        if self.error is not None:
            raise self.error
        self.published.append((body, options))


class FakeSession:
    """Minimal async SQLModel session double."""

    def __init__(self, notification: Notification | None) -> None:
        self.notification = notification
        self.commits = 0
        self.for_update: bool | None = None
        self.requested_id: UUID | None = None

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    async def get(
        self,
        model: type[Notification],
        notification_id: UUID,
        **kwargs: Any,
    ) -> Notification | None:
        self.requested_id = notification_id
        self.for_update = kwargs.get("with_for_update")
        return self.notification

    async def commit(self) -> None:
        self.commits += 1


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


def task_body(
    notification_id: str | UUID,
    *,
    args: list[Any] | None = None,
    kwargs: dict[str, Any] | None = None,
) -> list[Any]:
    """Build a protocol v2 Celery body."""

    return [
        [str(notification_id)] if args is None else args,
        {} if kwargs is None else kwargs,
        {},
    ]


def task_headers(
    retries: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build Celery task headers for a DLQ message."""

    headers: dict[str, Any] = {
        "task": TASK_NAME,
        "id": "task-id",
        "retries": 4,
    }
    if retries is not None:
        headers["x-retries"] = retries
    headers.update(extra)
    return headers


def test_protocol_v2_message_is_parsed() -> None:
    notification_id = str(uuid4())
    message = FakeMessage(
        task_body(notification_id),
        task_headers(),
    )

    call = parse_task_call(message)  # type: ignore[arg-type]

    assert call == TaskCall(TASK_NAME, (notification_id,), {})


def test_protocol_v1_message_is_parsed() -> None:
    notification_id = str(uuid4())
    message = FakeMessage(
        {
            "task": TASK_NAME,
            "args": [notification_id],
            "kwargs": {},
        },
        {},
    )

    call = parse_task_call(message)  # type: ignore[arg-type]

    assert call == TaskCall(TASK_NAME, (notification_id,), {})


def test_message_body_is_used_when_payload_is_absent() -> None:
    notification_id = str(uuid4())
    message = FakeMessage(None, task_headers())
    message.body = task_body(notification_id)

    call = parse_task_call(message)  # type: ignore[arg-type]

    assert call == TaskCall(TASK_NAME, (notification_id,), {})


@pytest.mark.parametrize(
    "payload",
    [
        "not a task body",
        [],
        [["only args"]],
        [[], {}, {}, "extra"],
        [["args"], "not a mapping", {}],
    ],
)
def test_malformed_bodies_are_rejected(payload: Any) -> None:
    message = FakeMessage(payload, task_headers())

    with pytest.raises(MalformedMessage):
        parse_task_call(message)  # type: ignore[arg-type]


def test_message_without_task_name_is_malformed() -> None:
    message = FakeMessage(task_body(str(uuid4())), {"id": "task-id"})

    with pytest.raises(MalformedMessage):
        parse_task_call(message)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, None),
        ({"x-retries": 0}, 0),
        ({"x-retries": 2}, 2),
        ({"x-retries": "2"}, 2),
    ],
)
def test_retry_count_is_read(
    headers: dict[str, Any],
    expected: int | None,
) -> None:
    assert read_retry_count(headers) == expected


@pytest.mark.parametrize(
    "value",
    [-1, "-1", True, "nope", object()],
)
def test_invalid_retry_counts_are_malformed(value: Any) -> None:
    with pytest.raises(MalformedMessage):
        read_retry_count({"x-retries": value})


def test_retry_headers_strip_death_state_and_reset_retries() -> None:
    headers = task_headers(
        2,
        **{
            "x-death": [{"queue": "task"}],
            "x-first-death-exchange": "task",
            "x-first-death-queue": "task",
            "x-first-death-reason": "rejected",
            "x-last-death-exchange": "task",
            "x-last-death-queue": "task",
            "x-last-death-reason": "rejected",
            "eta": "2026-01-01T00:00:00+00:00",
            "expires": "2026-01-01T00:00:00+00:00",
            "compression": "zlib",
            "custom": "value",
        },
    )

    cleaned = build_retry_headers(headers, 2)

    assert cleaned == {
        "task": TASK_NAME,
        "id": "task-id",
        "retries": 0,
        "x-retries": 2,
        "custom": "value",
    }


def test_handler_arguments_are_normalized_from_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    async def handler(notification_id: str) -> None:
        called.append(notification_id)

    monkeypatch.setitem(consumer.HANDLERS, TASK_NAME, handler)
    notification_id = str(uuid4())
    call = TaskCall(TASK_NAME, (), {"notification_id": notification_id})

    handler_call = resolve_handler(call)

    assert handler_call == HandlerCall(
        handler,
        {"notification_id": notification_id},
    )
    asyncio.run(invoke_handler(handler_call))
    assert called == [notification_id]


def test_unknown_task_is_acked_without_retry() -> None:
    message = FakeMessage(
        task_body(str(uuid4())),
        {"task": "unknown.task", "x-retries": 2},
    )
    producer = FakeProducer()

    handle_message(message, producer)  # type: ignore[arg-type]

    assert message.acked is True
    assert message.rejections == []
    assert producer.published == []


def test_handler_argument_mismatch_is_acked_as_poison() -> None:
    message = FakeMessage(
        task_body(str(uuid4()), args=[]),
        task_headers(2),
    )
    producer = FakeProducer()

    handle_message(message, producer)  # type: ignore[arg-type]

    assert message.acked is True
    assert message.rejections == []
    assert producer.published == []


def test_both_task_names_use_the_failure_handler() -> None:
    assert HANDLERS["notification.process_notification"] is mark_notif_failed
    assert HANDLERS["process_notification"] is mark_notif_failed


def test_missing_retry_header_publishes_first_retry() -> None:
    notification_id = str(uuid4())
    message = FakeMessage(
        task_body(notification_id),
        task_headers(),
    )
    producer = FakeProducer()

    handle_message(message, producer)  # type: ignore[arg-type]

    assert message.acked is True
    assert message.rejections == []
    assert len(producer.published) == 1
    body, options = producer.published[0]
    assert body == task_body(notification_id)
    assert options["routing_key"] == "task.retry"
    assert options["exchange"].name == "task.dlx"
    assert options["serializer"] == "json"
    assert options["headers"]["x-retries"] == 1
    assert options["headers"]["retries"] == 0


@pytest.mark.parametrize("retries", [0, 1])
def test_retry_below_limit_increments_and_republishes(retries: int) -> None:
    message = FakeMessage(
        task_body(str(uuid4())),
        task_headers(retries),
    )
    producer = FakeProducer()

    handle_message(message, producer)  # type: ignore[arg-type]

    assert message.acked is True
    assert len(producer.published) == 1
    assert producer.published[0][1]["headers"]["x-retries"] == retries + 1


def test_retry_limit_runs_terminal_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notification_id = str(uuid4())
    called: list[str] = []

    async def handler(value: str) -> None:
        called.append(value)

    monkeypatch.setitem(consumer.HANDLERS, TASK_NAME, handler)
    message = FakeMessage(
        task_body(notification_id),
        task_headers(2),
    )
    producer = FakeProducer()

    handle_message(message, producer)  # type: ignore[arg-type]

    assert called == [notification_id]
    assert message.acked is True
    assert message.rejections == []
    assert producer.published == []


def test_retry_limit_uses_shorthand_task_name_and_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notification_id = str(uuid4())
    called: list[str] = []

    async def handler(notification_id: str) -> None:
        called.append(notification_id)

    monkeypatch.setitem(consumer.HANDLERS, "process_notification", handler)
    message = FakeMessage(
        task_body(
            notification_id,
            args=[],
            kwargs={"notification_id": notification_id},
        ),
        {"task": "process_notification", "x-retries": 2},
    )
    producer = FakeProducer()

    handle_message(message, producer)  # type: ignore[arg-type]

    assert called == [notification_id]
    assert message.acked is True
    assert producer.published == []


def test_retry_limit_above_three_still_runs_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    async def handler(value: str) -> None:
        called.append(value)

    monkeypatch.setitem(consumer.HANDLERS, TASK_NAME, handler)
    message = FakeMessage(
        task_body(str(uuid4())),
        task_headers(4),
    )
    producer = FakeProducer()

    handle_message(message, producer)  # type: ignore[arg-type]

    assert len(called) == 1
    assert message.acked is True
    assert producer.published == []


def test_publish_failure_throttles_and_requeues() -> None:
    message = FakeMessage(
        task_body(str(uuid4())),
        task_headers(),
    )
    producer = FakeProducer(error=RuntimeError("broker unavailable"))
    sleeps: list[float] = []

    handle_message(message, producer, sleep=sleeps.append)  # type: ignore[arg-type]

    assert sleeps == [DLQ_RETRY_THROTTLE_SECONDS]
    assert message.acked is False
    assert message.rejections == [True]


def test_uninjected_sleep_is_resolved_from_the_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(consumer.time, "sleep", sleeps.append)
    message = FakeMessage(
        task_body(str(uuid4())),
        task_headers(),
    )
    producer = FakeProducer(error=RuntimeError("broker unavailable"))

    handle_message(message, producer)  # type: ignore[arg-type]

    assert sleeps == [DLQ_RETRY_THROTTLE_SECONDS]
    assert message.rejections == [True]


def test_handler_failure_throttles_and_requeues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(_: str) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setitem(consumer.HANDLERS, TASK_NAME, handler)
    message = FakeMessage(
        task_body(str(uuid4())),
        task_headers(2),
    )
    producer = FakeProducer()
    sleeps: list[float] = []

    handle_message(message, producer, sleep=sleeps.append)  # type: ignore[arg-type]

    assert sleeps == [DLQ_RETRY_THROTTLE_SECONDS]
    assert message.acked is False
    assert message.rejections == [True]
    assert producer.published == []


def test_invalid_notification_id_is_acked_as_poison() -> None:
    message = FakeMessage(
        task_body("not-a-uuid"),
        task_headers(2),
    )
    producer = FakeProducer()
    sleeps: list[float] = []

    handle_message(message, producer, sleep=sleeps.append)  # type: ignore[arg-type]

    assert message.acked is True
    assert message.rejections == []
    assert sleeps == []
    assert producer.published == []


def test_malformed_message_is_acked_without_retry() -> None:
    message = FakeMessage("not a body", task_headers(2))
    producer = FakeProducer()

    handle_message(message, producer)  # type: ignore[arg-type]

    assert message.acked is True
    assert message.rejections == []
    assert producer.published == []


def test_undecodable_message_is_acked() -> None:
    message = FakeMessage(b"\xff", task_headers(2))

    handle_decode_error(message, ValueError("bad json"))  # type: ignore[arg-type]

    assert message.acked is True
    assert message.rejections == []


def test_connection_enables_publisher_confirms() -> None:
    connection = consumer.create_connection()

    assert connection.transport_options["confirm_publish"] is True
    connection.release()


def test_pending_notification_is_marked_failed() -> None:
    notification = make_notification()
    session = FakeSession(notification)

    asyncio.run(_mark_notif_failed(notification.notification_id, lambda: session))

    assert session.requested_id == notification.notification_id
    assert session.for_update is True
    assert session.commits == 1
    assert notification.status is NotificationStatus.FAILED


@pytest.mark.parametrize(
    "status",
    [NotificationStatus.PROCESSED, NotificationStatus.FAILED],
)
def test_terminal_notification_status_is_left_unchanged(
    status: NotificationStatus,
) -> None:
    notification = make_notification(status)
    session = FakeSession(notification)

    asyncio.run(_mark_notif_failed(notification.notification_id, lambda: session))

    assert session.commits == 0
    assert notification.status is status


def test_missing_notification_is_ignored() -> None:
    session = FakeSession(None)

    asyncio.run(_mark_notif_failed(uuid4(), lambda: session))

    assert session.commits == 0


def test_mark_notif_failed_rejects_invalid_uuid_without_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_engine() -> None:
        pytest.fail("Database must not be touched for an invalid notification id")

    monkeypatch.setattr("dlq.handlers.create_postgresql_engine", fail_engine)

    with pytest.raises(InvalidNotificationId):
        asyncio.run(mark_notif_failed("not-a-uuid"))


def test_mark_notif_failed_accepts_uuid_values() -> None:
    notification = make_notification()
    session = FakeSession(notification)

    asyncio.run(
        _mark_notif_failed(
            UUID(str(notification.notification_id)),
            lambda: session,
        )
    )

    assert notification.status is NotificationStatus.FAILED
