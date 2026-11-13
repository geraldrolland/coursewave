"""Tests for the instructor registration endpoint."""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError

from app.base_dependencies import get_session
from app.main import create_app
from app.security import verify_hash
from app.services.instructor.model import AuthMethod, Instructor
from app.services.instructor.utils import (
    VERIFICATION_TOKEN_TTL_SECONDS,
    build_verification_link,
)
from app.services.notification.model import (
    Notification,
    NotificationAudience,
    NotificationStatus,
    NotificationType,
)
from app.services.outbox.model import Outbox, OutboxStatus
from tests.fakes import FakeRedis
from tests.helpers import build_settings

REGISTER_PATH = "/instructor/api/v1/register"

VALID_PAYLOAD = {
    "email": "ada@example.com",
    "password": "StrongPass1!",
    "confirm_password": "StrongPass1!",
}


class FakeTransaction:
    """Minimal async transaction context manager for a fake session."""

    def __init__(self, session: "FakeSession") -> None:
        self.session = session

    async def __aenter__(self) -> "FakeTransaction":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> bool:
        if exc_type is None:
            if self.session.commit_error is not None:
                self.session.rolled_back = True
                raise self.session.commit_error

            self.session.committed = True
        else:
            self.session.rolled_back = True

        return False


class FakeSession:
    """Fake SQLModel async session used to observe transaction behavior."""

    def __init__(
        self,
        flush_error: Exception | None = None,
        commit_error: Exception | None = None,
    ) -> None:
        self.added: list[Any] = []
        self.flush_error = flush_error
        self.commit_error = commit_error
        self.flushed = False
        self.committed = False
        self.rolled_back = False

    def add(self, model: Any) -> None:
        self.added.append(model)

    async def flush(self) -> None:
        self.flushed = True
        if self.flush_error is not None:
            raise self.flush_error

    def begin(self) -> FakeTransaction:
        return FakeTransaction(self)


@contextmanager
def registration_client(
    session: FakeSession,
    redis: FakeRedis,
    app_host: str = "localhost:3000",
) -> Iterator[TestClient]:
    """Create an app with fake database and Redis dependencies."""

    settings = build_settings(
        app_host=app_host,
        auto_create_tables=False,
        cache_enabled=False,
        rate_limit_enabled=False,
    )
    app = create_app(settings, redis_client=redis)  # type: ignore[arg-type]

    async def override_get_session() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def find_added(session: FakeSession, model_type: type[Any]) -> Any:
    """Return the single added model instance of the requested type."""

    matches = [model for model in session.added if isinstance(model, model_type)]
    assert len(matches) == 1
    return matches[0]


def test_registration_creates_instructor_notification_and_outbox() -> None:
    session = FakeSession()
    redis = FakeRedis()

    with registration_client(session, redis) as client:
        response = client.post(
            REGISTER_PATH,
            json={
                **VALID_PAYLOAD,
                "email": "  ADA@EXAMPLE.COM  ",
            },
        )

    assert response.status_code == 201
    assert response.json()["instructor_id"]
    assert response.json()["message"] == (
        "Registration successful. Please verify your email."
    )

    instructor = find_added(session, Instructor)
    notification = find_added(session, Notification)
    outbox = find_added(session, Outbox)

    assert len(session.added) == 3
    assert response.json()["instructor_id"] == str(instructor.instructor_id)
    assert instructor.email == "ada@example.com"
    assert instructor.is_email_verified is False
    assert instructor.is_2fa_enabled is False
    assert instructor.auth_method is AuthMethod.EMAIL
    assert instructor.password_hash is not None
    assert instructor.password_hash != VALID_PAYLOAD["password"]
    assert verify_hash(VALID_PAYLOAD["password"], instructor.password_hash)

    assert session.flushed is True
    assert session.committed is True
    assert session.rolled_back is False

    assert len(redis.values) == 1
    token, stored_instructor_id = next(iter(redis.values.items()))
    assert stored_instructor_id == str(instructor.instructor_id)
    assert redis.ttls[token] == VERIFICATION_TOKEN_TTL_SECONDS

    verification_link = (
        f"http://localhost:3000/email-verification?token={token}"
    )
    assert notification.type is NotificationType.EMAIL
    assert notification.belongs_to is NotificationAudience.INSTRUCTOR
    assert notification.user_id == instructor.instructor_id
    assert notification.status is NotificationStatus.PENDING
    assert notification.payload == {
        "template_type": "email_verification",
        "content": {"link": verification_link},
        "to_email": "ada@example.com",
    }

    assert outbox.status is OutboxStatus.PENDING
    assert outbox.retry_after is None
    assert outbox.num_of_retries == 0
    assert outbox.table == "notification"
    assert outbox.ref_id == str(notification.notification_id)
    assert outbox.payload == {
        "argument": {
            "notification_id": str(notification.notification_id),
        },
        "task_name": "process_notification",
    }


def test_duplicate_email_returns_409_and_rolls_back() -> None:
    duplicate_error = IntegrityError(
        "INSERT INTO instructor",
        {},
        Exception("duplicate key value violates unique constraint"),
    )
    session = FakeSession(flush_error=duplicate_error)
    redis = FakeRedis()

    with registration_client(session, redis) as client:
        response = client.post(
            REGISTER_PATH,
            json=VALID_PAYLOAD,
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "Email is already registered"}
    assert session.rolled_back is True
    assert session.committed is False
    assert session.flushed is True
    assert redis.values == {}
    assert redis.deleted == []
    assert find_added(session, Instructor).email == "ada@example.com"


def test_redis_failure_returns_503_and_rolls_back() -> None:
    session = FakeSession()
    redis = FakeRedis()
    redis.fail("set", RedisError("redis unavailable"))

    with registration_client(session, redis) as client:
        response = client.post(
            REGISTER_PATH,
            json=VALID_PAYLOAD,
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Email verification service unavailable"
    }
    assert session.rolled_back is True
    assert session.committed is False
    assert session.flushed is True
    assert redis.values == {}


def test_commit_failure_cleans_up_verification_token() -> None:
    session = FakeSession(commit_error=RuntimeError("commit failed"))
    redis = FakeRedis()

    with registration_client(session, redis) as client:
        response = client.post(
            REGISTER_PATH,
            json=VALID_PAYLOAD,
        )

    assert response.status_code == 500
    assert session.rolled_back is True
    assert session.committed is False
    assert redis.values == {}
    assert len(redis.deleted) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"password": "too-weak", "confirm_password": "too-weak"},
        {"confirm_password": "DifferentPass1!"},
        {"email": "not-an-email"},
        {"first_name": "Ada"},
    ],
)
def test_invalid_registration_payload_returns_422_without_writes(
    overrides: dict[str, str],
) -> None:
    session = FakeSession()
    redis = FakeRedis()

    with registration_client(session, redis) as client:
        response = client.post(
            REGISTER_PATH,
            json={**VALID_PAYLOAD, **overrides},
        )

    assert response.status_code == 422
    assert session.added == []
    assert redis.values == {}


def test_build_verification_link_adds_http_scheme_when_missing() -> None:
    assert build_verification_link("localhost:3000/", "abc") == (
        "http://localhost:3000/email-verification?token=abc"
    )


def test_registration_uses_https_app_host_when_provided() -> None:
    session = FakeSession()
    redis = FakeRedis()

    with registration_client(
        session,
        redis,
        app_host="https://coursewave.example",
    ) as client:
        response = client.post(
            REGISTER_PATH,
            json=VALID_PAYLOAD,
        )

    assert response.status_code == 201
    token = next(iter(redis.values))
    notification = find_added(session, Notification)
    assert notification.payload["content"]["link"] == (
        f"https://coursewave.example/email-verification?token={token}"
    )
