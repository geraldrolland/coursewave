"""Tests for the instructor login endpoint."""

import json
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app.base_dependencies import get_session
from app.main import create_app
from app.security import hash, verify_hash
from app.services.instructor.model import Instructor
from app.services.instructor.utils import (
    AUTH_UNAVAILABLE,
    EMAIL_VERIFICATION_UNAVAILABLE,
    OTP_CODE_TTL_SECONDS,
    OTP_TOKEN_TTL_SECONDS,
    VERIFICATION_TOKEN_TTL_SECONDS,
    build_otp_verification_link,
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
from tests.helpers import TEST_JWT_SECRET, build_settings

LOGIN_PATH = "/instructor/api/v1/login"
DEFAULT_EMAIL = "ada@example.com"
DEFAULT_PASSWORD = "StrongPass1!"


class FakeScalarResult:
    """Minimal scalar result double for ``AsyncSession.execute``."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class FakeSession:
    """Minimal async session double for instructor login routes."""

    def __init__(
        self,
        instructor: Instructor | None = None,
        commit_error: Exception | None = None,
    ) -> None:
        self.instructor = instructor
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0
        self.commit_error = commit_error

    async def execute(self, statement: Any) -> FakeScalarResult:
        return FakeScalarResult(self.instructor)

    def add(self, model: Any) -> None:
        self.added.append(model)

    async def commit(self) -> None:
        if self.commit_error is not None:
            raise self.commit_error
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@contextmanager
def login_client(
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


def make_instructor(
    *,
    email: str = DEFAULT_EMAIL,
    password: str | None = DEFAULT_PASSWORD,
    is_email_verified: bool = True,
    is_2fa_enabled: bool = False,
) -> Instructor:
    """Build an instructor with a real password hash by default."""

    password_hash = hash(password) if password is not None else None
    return Instructor(
        email=email,
        password_hash=password_hash,
        is_email_verified=is_email_verified,
        is_2fa_enabled=is_2fa_enabled,
    )


def find_added(session: FakeSession, model_type: type[Any]) -> Any:
    """Return the single added model instance of the requested type."""

    matches = [model for model in session.added if isinstance(model, model_type)]
    assert len(matches) == 1
    return matches[0]


def test_login_without_2fa_returns_tokens_and_session() -> None:
    instructor = make_instructor()
    session = FakeSession(instructor)
    redis = FakeRedis()

    with login_client(session, redis) as client:
        response = client.post(
            LOGIN_PATH,
            json={
                "email": f"  {DEFAULT_EMAIL.upper()}  ",
                "password": DEFAULT_PASSWORD,
            },
        )

    assert response.status_code == 200
    assert set(response.json()) == {"accessToken"}
    access_payload = jwt.decode(
        response.json()["accessToken"],
        TEST_JWT_SECRET,
        algorithms=["HS256"],
    )
    assert access_payload["type"] == "access"
    session_id = access_payload["sessionId"]
    session_key = f"session:{session_id}"

    assert redis.values[session_key] == json.dumps(
        {
            "id": str(instructor.instructor_id),
            "email": DEFAULT_EMAIL,
            "role": "instructor",
        }
    )
    assert redis.ttls[session_key] == 604800
    assert session.added == []
    assert session.commits == 0

    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith("refresh=")
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=Strict" in set_cookie
    assert "Path=/auth/api/v1/token-refresh" in set_cookie
    assert "Max-Age=604800" in set_cookie


def test_login_unknown_email_returns_generic_401() -> None:
    session = FakeSession()
    redis = FakeRedis()

    with login_client(session, redis) as client:
        response = client.post(
            LOGIN_PATH,
            json={"email": "missing@example.com", "password": DEFAULT_PASSWORD},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}
    assert response.headers.get("set-cookie") is None
    assert redis.values == {}


def test_login_wrong_password_returns_generic_401() -> None:
    instructor = make_instructor()
    session = FakeSession(instructor)
    redis = FakeRedis()

    with login_client(session, redis) as client:
        response = client.post(
            LOGIN_PATH,
            json={"email": DEFAULT_EMAIL, "password": "WrongPass1!"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}
    assert response.headers.get("set-cookie") is None
    assert redis.values == {}


def test_login_google_only_account_returns_generic_401() -> None:
    instructor = make_instructor(password=None)
    session = FakeSession(instructor)
    redis = FakeRedis()

    with login_client(session, redis) as client:
        response = client.post(
            LOGIN_PATH,
            json={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}
    assert response.headers.get("set-cookie") is None
    assert redis.values == {}


def test_login_unverified_email_resends_verification() -> None:
    instructor = make_instructor(is_email_verified=False)
    session = FakeSession(instructor)
    redis = FakeRedis()

    with login_client(session, redis) as client:
        response = client.post(
            LOGIN_PATH,
            json={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD},
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Email is not verified. A verification email has been sent."
    }
    assert response.headers.get("set-cookie") is None
    assert session.commits == 1
    assert not any(key.startswith("session:") for key in redis.values)
    assert len(redis.values) == 1

    token, stored_instructor_id = next(iter(redis.values.items()))
    assert stored_instructor_id == str(instructor.instructor_id)
    assert redis.ttls[token] == VERIFICATION_TOKEN_TTL_SECONDS

    notification = find_added(session, Notification)
    outbox = find_added(session, Outbox)
    assert notification.type is NotificationType.EMAIL
    assert notification.belongs_to is NotificationAudience.INSTRUCTOR
    assert notification.user_id == instructor.instructor_id
    assert notification.status is NotificationStatus.PENDING
    assert notification.payload == {
        "template_type": "email_verification",
        "content": {
            "link": build_verification_link("localhost:3000", token),
        },
        "to_email": DEFAULT_EMAIL,
    }
    assert outbox.status is OutboxStatus.PENDING
    assert outbox.table == "notification"
    assert outbox.ref_id == str(notification.notification_id)
    assert outbox.payload == {
        "argument": {
            "notification_id": str(notification.notification_id),
        },
        "task_name": "process_notification",
    }


def test_login_unverified_redis_failure_returns_503() -> None:
    instructor = make_instructor(is_email_verified=False)
    session = FakeSession(instructor)
    redis = FakeRedis()
    redis.fail("set", RedisError("redis unavailable"))

    with login_client(session, redis) as client:
        response = client.post(
            LOGIN_PATH,
            json={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": EMAIL_VERIFICATION_UNAVAILABLE
    }
    assert session.rollbacks == 1
    assert session.added == []
    assert redis.values == {}


def test_login_2fa_redirects_and_queues_otp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.instructor.utils.secrets.randbelow",
        lambda _: 42,
    )
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeSession(instructor)
    redis = FakeRedis()
    before = int(time.time())

    with login_client(
        session,
        redis,
        app_host="https://coursewave.example",
    ) as client:
        response = client.post(
            LOGIN_PATH,
            json={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD},
            follow_redirects=False,
        )

    after = int(time.time())

    assert response.status_code == 302
    assert response.headers.get("set-cookie") is None
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.scheme == "https"
    assert parsed.netloc == "coursewave.example"
    assert parsed.path == "/otp-verification"
    token = parse_qs(parsed.query)["token"][0]

    assert session.commits == 1
    assert session.rollbacks == 0
    assert len(redis.values) == 1
    assert redis.ttls[token] == OTP_TOKEN_TTL_SECONDS
    assert OTP_TOKEN_TTL_SECONDS == 86400
    challenge = json.loads(redis.values[token])
    assert set(challenge) == {"instructor_id", "code_hash", "exp"}
    assert challenge["instructor_id"] == str(instructor.instructor_id)
    assert verify_hash("000042", challenge["code_hash"])
    assert not verify_hash("000043", challenge["code_hash"])
    assert (
        before + OTP_CODE_TTL_SECONDS
        <= challenge["exp"]
        <= after + OTP_CODE_TTL_SECONDS
    )
    assert not any(key.startswith("session:") for key in redis.values)

    notification = find_added(session, Notification)
    outbox = find_added(session, Outbox)
    assert notification.type is NotificationType.EMAIL
    assert notification.belongs_to is NotificationAudience.INSTRUCTOR
    assert notification.user_id == instructor.instructor_id
    assert notification.status is NotificationStatus.PENDING
    assert notification.payload == {
        "template_type": "otp",
        "content": {"otp_code": "000042"},
        "to_email": DEFAULT_EMAIL,
    }
    assert outbox.status is OutboxStatus.PENDING
    assert outbox.table == "notification"
    assert outbox.ref_id == str(notification.notification_id)
    assert outbox.payload == {
        "argument": {
            "notification_id": str(notification.notification_id),
        },
        "task_name": "process_notification",
    }


def test_login_2fa_redis_failure_returns_503() -> None:
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeSession(instructor)
    redis = FakeRedis()
    redis.fail("set", RedisError("redis unavailable"))

    with login_client(session, redis) as client:
        response = client.post(
            LOGIN_PATH,
            json={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD},
            follow_redirects=False,
        )

    assert response.status_code == 503
    assert response.json() == {"detail": AUTH_UNAVAILABLE}
    assert session.rollbacks == 1
    assert session.added == []
    assert redis.values == {}


def test_login_2fa_commit_failure_cleans_up_token() -> None:
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeSession(
        instructor,
        commit_error=RuntimeError("commit failed"),
    )
    redis = FakeRedis()

    with login_client(session, redis) as client:
        response = client.post(
            LOGIN_PATH,
            json={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD},
            follow_redirects=False,
        )

    assert response.status_code == 500
    assert session.rollbacks == 1
    assert redis.values == {}
    assert len(redis.deleted) == 1


def test_login_session_redis_failure_returns_503() -> None:
    instructor = make_instructor()
    session = FakeSession(instructor)
    redis = FakeRedis()
    redis.fail("set", RedisError("redis unavailable"))

    with login_client(session, redis) as client:
        response = client.post(
            LOGIN_PATH,
            json={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": AUTH_UNAVAILABLE}
    assert redis.values == {}


def test_login_rejects_extra_fields() -> None:
    instructor = make_instructor()
    session = FakeSession(instructor)
    redis = FakeRedis()

    with login_client(session, redis) as client:
        response = client.post(
            LOGIN_PATH,
            json={
                "email": DEFAULT_EMAIL,
                "password": DEFAULT_PASSWORD,
                "role": "admin",
            },
        )

    assert response.status_code == 422
    assert session.added == []
    assert redis.values == {}


def test_build_otp_verification_link_adds_http_scheme_when_missing() -> None:
    assert build_otp_verification_link("localhost:3000/", "abc") == (
        "http://localhost:3000/otp-verification?token=abc"
    )
