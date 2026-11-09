"""Tests for the transaction outbox and its Celery Beat dispatcher."""

import asyncio
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.base_utils import utc_now
from app.services.notification.model import (
    Notification,
    NotificationAudience,
    NotificationStatus,
    NotificationType,
)
from app.services.outbox.model import Outbox, OutboxStatus
from app.services.outbox.task.process_outbox import (
    OUTBOX_LOCK_TTL_SECONDS,
    OUTBOX_MAX_RETRIES,
    OUTBOX_RETRY_BASE_SECONDS,
    OUTBOX_TASK_NAME,
    OutboxPayloadError,
    load_due_outbox_ids,
    parse_outbox_payload,
    process_outbox,
    process_outbox_row,
    process_pending_outbox,
)
from celery_app import celery_app
from celery_app.task_registry import TASK_REGISTRY
from tests.fakes import FakeRedis


class FakeScalarResult:
    """Minimal scalar result double for ``AsyncSession.execute``."""

    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def scalars(self) -> "FakeScalarResult":
        return self

    def all(self) -> list[Any]:
        return list(self.values)


class FakeSession:
    """Minimal async session double for outbox processing."""

    def __init__(
        self,
        outbox: Outbox | None = None,
        notification: Notification | None = None,
        due_ids: list[UUID] | None = None,
    ) -> None:
        self.outbox = outbox
        self.notification = notification
        self.due_ids = due_ids or []
        self.executed: list[Any] = []
        self.get_calls: list[tuple[Any, Any, dict[str, Any]]] = []
        self.commits = 0

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    async def execute(self, statement: Any) -> FakeScalarResult:
        self.executed.append(statement)
        return FakeScalarResult(self.due_ids)

    async def get(
        self,
        model: type[Any],
        identifier: Any,
        **kwargs: Any,
    ) -> Any:
        self.get_calls.append((model, identifier, kwargs))
        if model is Outbox:
            if self.outbox is not None and self.outbox.id == identifier:
                return self.outbox
            return None
        if model is Notification:
            if (
                self.notification is not None
                and self.notification.notification_id == identifier
            ):
                return self.notification
            return None
        return None

    async def commit(self) -> None:
        self.commits += 1


class FakeSessionFactory:
    """Create fake sessions and retain them for assertions."""

    def __init__(
        self,
        outbox: Outbox | None = None,
        notification: Notification | None = None,
        due_ids: list[UUID] | None = None,
    ) -> None:
        self.outbox = outbox
        self.notification = notification
        self.due_ids = list(due_ids or [])
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession(
            outbox=self.outbox,
            notification=self.notification,
            due_ids=self.due_ids,
        )
        self.sessions.append(session)
        return session


def make_notification(
    status: NotificationStatus = NotificationStatus.PENDING,
) -> Notification:
    """Build a notification with predictable defaults."""

    return Notification(
        type=NotificationType.EMAIL,
        payload={"template_type": "email_verification", "content": {}},
        belongs_to=NotificationAudience.STUDENT,
        user_id=uuid4(),
        status=status,
    )


def make_outbox(
    num_of_retries: int = 0,
    payload: dict[str, Any] | None = None,
    ref_id: str | None = None,
) -> Outbox:
    """Build a pending outbox row with predictable defaults."""

    return Outbox(
        status=OutboxStatus.PENDING,
        num_of_retries=num_of_retries,
        payload=payload
        or {
            "argument": {"notification_id": str(uuid4())},
            "task_name": "process_notification",
        },
        table="notification",
        ref_id=ref_id or str(uuid4()),
    )


def test_outbox_model_defaults_and_enums() -> None:
    outbox = make_outbox()

    assert outbox.id is not None
    assert outbox.status is OutboxStatus.PENDING
    assert outbox.retry_after is None
    assert outbox.num_of_retries == 0
    assert outbox.table == "notification"
    assert [member.value for member in OutboxStatus] == [
        "pending",
        "processed",
        "failed",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"task_name": "process_notification"},
        {"argument": {}},
        {"task_name": "", "argument": {}},
        {"task_name": "process_notification", "argument": []},
    ],
)
def test_parse_outbox_payload_rejects_invalid_shapes(payload: Any) -> None:
    with pytest.raises(OutboxPayloadError):
        parse_outbox_payload(payload)


def test_load_due_outbox_ids_queries_pending_and_retry_after() -> None:
    due_ids = [uuid4(), uuid4()]
    factory = FakeSessionFactory(due_ids=due_ids)

    result = asyncio.run(load_due_outbox_ids(factory))

    assert result == due_ids
    statement = str(factory.sessions[0].executed[0])
    assert "outbox.status" in statement
    assert "outbox.retry_after" in statement
    assert "LIMIT" in statement


def test_process_outbox_row_dispatches_and_marks_processed() -> None:
    notification = make_notification()
    outbox = make_outbox(
        payload={
            "argument": {
                "notification_id": str(notification.notification_id),
            },
            "task_name": "process_notification",
        },
        ref_id=str(notification.notification_id),
    )
    factory = FakeSessionFactory(outbox=outbox, notification=notification)
    redis = FakeRedis()
    calls: list[dict[str, Any]] = []

    def fake_task(**kwargs: Any) -> None:
        calls.append(kwargs)

    processed = asyncio.run(
        process_outbox_row(
            outbox.id,
            redis,
            factory,
            {"process_notification": fake_task},
        )
    )

    assert processed is True
    assert calls == [
        {"notification_id": str(notification.notification_id)}
    ]
    assert outbox.status is OutboxStatus.PROCESSED
    assert outbox.retry_after is None
    assert factory.sessions[0].commits == 1
    assert f"outbox:process:{outbox.id}" not in redis.values


def test_outbox_failure_schedules_retry() -> None:
    outbox = make_outbox()
    factory = FakeSessionFactory(outbox=outbox)
    redis = FakeRedis()

    def failing_task(**_: Any) -> None:
        raise RuntimeError("delivery failed")

    before = utc_now()
    processed = asyncio.run(
        process_outbox_row(
            outbox.id,
            redis,
            factory,
            {"process_notification": failing_task},
        )
    )
    after = utc_now()

    assert processed is False
    assert outbox.status is OutboxStatus.PENDING
    assert outbox.num_of_retries == 1
    assert outbox.retry_after is not None
    expected_delay = timedelta(seconds=OUTBOX_RETRY_BASE_SECONDS)
    assert before + expected_delay <= outbox.retry_after
    assert outbox.retry_after <= after + expected_delay
    assert factory.sessions[0].commits == 1
    assert f"outbox:process:{outbox.id}" not in redis.values


def test_outbox_failure_after_max_retries_marks_notification_failed() -> None:
    notification = make_notification()
    outbox = make_outbox(
        num_of_retries=OUTBOX_MAX_RETRIES - 1,
        ref_id=str(notification.notification_id),
    )
    factory = FakeSessionFactory(outbox=outbox, notification=notification)
    redis = FakeRedis()

    def failing_task(**_: Any) -> None:
        raise RuntimeError("delivery failed")

    processed = asyncio.run(
        process_outbox_row(
            outbox.id,
            redis,
            factory,
            {"process_notification": failing_task},
        )
    )

    assert processed is False
    assert outbox.status is OutboxStatus.FAILED
    assert outbox.num_of_retries == OUTBOX_MAX_RETRIES
    assert outbox.retry_after is None
    assert notification.status is NotificationStatus.FAILED
    assert factory.sessions[0].commits == 1
    assert f"outbox:process:{outbox.id}" not in redis.values


def test_outbox_lock_contention_skips_without_incrementing_retries() -> None:
    outbox = make_outbox()
    factory = FakeSessionFactory(outbox=outbox)
    redis = FakeRedis()
    redis.seed(f"outbox:process:{outbox.id}", "another-worker")
    calls: list[dict[str, Any]] = []

    def fake_task(**kwargs: Any) -> None:
        calls.append(kwargs)

    processed = asyncio.run(
        process_outbox_row(
            outbox.id,
            redis,
            factory,
            {"process_notification": fake_task},
        )
    )

    assert processed is False
    assert calls == []
    assert factory.sessions == []
    assert outbox.num_of_retries == 0
    assert outbox.status is OutboxStatus.PENDING


def test_unknown_task_uses_retry_budget() -> None:
    outbox = make_outbox(
        payload={"argument": {}, "task_name": "not_registered"},
    )
    factory = FakeSessionFactory(outbox=outbox)
    redis = FakeRedis()

    processed = asyncio.run(
        process_outbox_row(
            outbox.id,
            redis,
            factory,
            {},
        )
    )

    assert processed is False
    assert outbox.num_of_retries == 1
    assert outbox.status is OutboxStatus.PENDING
    assert outbox.retry_after is not None


def test_process_pending_outbox_processes_due_batch() -> None:
    notification = make_notification()
    outbox = make_outbox(
        payload={
            "argument": {
                "notification_id": str(notification.notification_id),
            },
            "task_name": "process_notification",
        },
        ref_id=str(notification.notification_id),
    )
    factory = FakeSessionFactory(
        outbox=outbox,
        notification=notification,
        due_ids=[outbox.id],
    )
    redis = FakeRedis()
    calls: list[dict[str, Any]] = []

    def fake_task(**kwargs: Any) -> None:
        calls.append(kwargs)

    processed = asyncio.run(
        process_pending_outbox(
            redis,
            factory,
            {"process_notification": fake_task},
        )
    )

    assert processed == 1
    assert calls == [
        {"notification_id": str(notification.notification_id)}
    ]
    assert outbox.status is OutboxStatus.PROCESSED
    assert len(factory.sessions) == 2
    assert factory.sessions[0].executed


def test_task_registry_contains_defined_tasks() -> None:
    assert TASK_REGISTRY["process_notification"].name == (
        "notification.process_notification"
    )
    assert TASK_REGISTRY["process_outbox"].name == OUTBOX_TASK_NAME


def test_outbox_beat_schedule_runs_every_ten_seconds() -> None:
    schedule = celery_app.conf.beat_schedule["process-outbox-every-10-seconds"]

    assert schedule["task"] == OUTBOX_TASK_NAME
    assert schedule["schedule"] == 10.0
    assert process_outbox.name == OUTBOX_TASK_NAME
    assert OUTBOX_LOCK_TTL_SECONDS == 60
