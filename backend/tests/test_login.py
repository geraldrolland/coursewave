"""Tests for the student login endpoint."""

import json

import jwt
import pytest

from app.security import unsign_cookie_value, verify_password
from app.services.notification.model import (
    Notification,
    NotificationAudience,
    NotificationType,
)
from app.services.outbox.model import Outbox, OutboxStatus
from app.services.student.router.student import (
    AUTH_UNAVAILABLE,
    EMAIL_NOT_VERIFIED,
    INVALID_CREDENTIALS,
    VERIFICATION_TOKEN_TTL_SECONDS,
    build_verification_link,
)
from tests.fakes import FakeRedis
from tests.helpers import TEST_COOKIE_SECRET, TEST_JWT_SECRET
from tests.student_fakes import (
    DEFAULT_EMAIL,
    DEFAULT_PASSWORD,
    FakeStudentSession,
    make_student,
    student_client,
)

LOGIN_PATH = "/student/api/v1/login"
REFRESH_PATH = "/auth/api/v1/token-refresh"


def test_login_success_creates_session_and_refresh_cookie() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()

    with student_client(session, redis) as client:
        response = client.post(
            LOGIN_PATH,
            json={
                "email": f"  {DEFAULT_EMAIL.upper()}  ",
                "password": DEFAULT_PASSWORD,
            },
        )

        assert response.status_code == 200
        access_token = response.json()["accessToken"]
        access_payload = jwt.decode(
            access_token,
            TEST_JWT_SECRET,
            algorithms=["HS256"],
        )

        assert access_payload["type"] == "access"
        session_id = access_payload["sessionId"]
        session_key = f"session:{session_id}"

        assert redis.values[session_key] == json.dumps(
            {
                "userId": str(student.user_id),
                "role": "student",
                "email": DEFAULT_EMAIL,
            }
        )
        assert redis.ttls[session_key] == 604800

        set_cookie = response.headers["set-cookie"]
        assert set_cookie.startswith("refresh=")
        assert "HttpOnly" in set_cookie
        assert "Secure" in set_cookie
        assert "SameSite=Strict" in set_cookie
        assert f"Path={REFRESH_PATH}" in set_cookie
        assert "Max-Age=604800" in set_cookie

        cookie_value = set_cookie.split(";", 1)[0].split("=", 1)[1]
        refresh_token = unsign_cookie_value(
            cookie_value,
            TEST_COOKIE_SECRET,
        )
        assert refresh_token is not None
        refresh_payload = jwt.decode(
            refresh_token,
            TEST_JWT_SECRET,
            algorithms=["HS256"],
        )
        assert refresh_payload["type"] == "refresh"
        assert refresh_payload["sessionId"] == session_id

        cookie_header = set_cookie.split(";", 1)[0]
        refresh_response = client.post(
            REFRESH_PATH,
            headers={"Cookie": cookie_header},
        )
        assert refresh_response.status_code == 200
        assert refresh_response.json()["accessToken"]

    assert session.commits == 0


def test_login_unknown_email_returns_generic_401() -> None:
    session = FakeStudentSession()
    redis = FakeRedis()

    with student_client(session, redis) as client:
        response = client.post(
            LOGIN_PATH,
            json={"email": "missing@example.com", "password": DEFAULT_PASSWORD},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_CREDENTIALS}
    assert session.added == []
    assert redis.values == {}


def test_login_wrong_password_returns_generic_401() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()

    with student_client(session, redis) as client:
        response = client.post(
            LOGIN_PATH,
            json={"email": DEFAULT_EMAIL, "password": "WrongPass1!"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_CREDENTIALS}
    assert verify_password(DEFAULT_PASSWORD, student.password_hash)
    assert session.added == []
    assert redis.values == {}


def test_login_google_only_account_returns_generic_401() -> None:
    student = make_student(password=None)
    session = FakeStudentSession(student)
    redis = FakeRedis()

    with student_client(session, redis) as client:
        response = client.post(
            LOGIN_PATH,
            json={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_CREDENTIALS}
    assert redis.values == {}


def test_login_unverified_queues_verification_email_and_returns_403() -> None:
    student = make_student(is_email_verified=False)
    session = FakeStudentSession(student)
    redis = FakeRedis()

    with student_client(session, redis) as client:
        response = client.post(
            LOGIN_PATH,
            json={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": EMAIL_NOT_VERIFIED}
    assert not any(key.startswith("session:") for key in redis.values)
    assert len(redis.values) == 1

    token, stored_user_id = next(iter(redis.values.items()))
    assert stored_user_id == str(student.user_id)
    assert redis.ttls[token] == VERIFICATION_TOKEN_TTL_SECONDS

    notifications = [
        model for model in session.added if isinstance(model, Notification)
    ]
    outboxes = [model for model in session.added if isinstance(model, Outbox)]
    assert len(notifications) == 1
    assert len(outboxes) == 1

    notification = notifications[0]
    outbox = outboxes[0]
    assert notification.type is NotificationType.EMAIL
    assert notification.belongs_to is NotificationAudience.STUDENT
    assert notification.user_id == student.user_id
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
    assert session.commits == 1


def test_login_cleans_up_token_when_unverified_commit_fails() -> None:
    student = make_student(is_email_verified=False)
    session = FakeStudentSession(student)
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()

    with student_client(session, redis) as client:
        with pytest.raises(RuntimeError):
            client.post(
                LOGIN_PATH,
                json={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD},
            )

    assert redis.values == {}
    assert session.rollbacks == 1


def test_login_redis_failure_returns_503() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()
    redis.fail("set")

    with student_client(session, redis) as client:
        response = client.post(
            LOGIN_PATH,
            json={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": AUTH_UNAVAILABLE}
    assert redis.values == {}


def test_login_rejects_extra_fields() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()

    with student_client(session, redis) as client:
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
