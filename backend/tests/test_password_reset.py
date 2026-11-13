"""Tests for password reset email and password reset endpoints."""

from uuid import uuid4

import pytest

from app.security import verify_hash
from app.services.notification.model import (
    Notification,
    NotificationAudience,
    NotificationType,
)
from app.services.outbox.model import Outbox, OutboxStatus
from app.services.student.router import (
    INVALID_PASSWORD_RESET_TOKEN,
    PASSWORD_RESET_SENT,
    PASSWORD_RESET_TOKEN_TTL_SECONDS,
    PASSWORD_RESET_UNAVAILABLE,
    STUDENT_NOT_FOUND,
)
from app.services.student.utils import build_password_reset_link
from tests.fakes import FakeRedis
from tests.student_fakes import (
    DEFAULT_EMAIL,
    DEFAULT_PASSWORD,
    FakeStudentSession,
    make_student,
    student_client,
)

EMAIL_PATH = "/student/api/v1/email"
RESET_PATH = "/student/api/v1/password-reset"
TOKEN = "password-reset-token"
NEW_PASSWORD = "NewStrong2!"


def test_password_reset_token_ttl_is_five_minutes() -> None:
    assert PASSWORD_RESET_TOKEN_TTL_SECONDS == 300


def test_email_endpoint_queues_password_reset_notification() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()

    with student_client(session, redis) as client:
        response = client.post(
            EMAIL_PATH,
            json={"email": f"  {DEFAULT_EMAIL.upper()}  "},
        )

    assert response.status_code == 200
    assert response.json() == {"message": PASSWORD_RESET_SENT}
    assert len(redis.values) == 1

    token, stored_user_id = next(iter(redis.values.items()))
    assert stored_user_id == str(student.user_id)
    assert redis.ttls[token] == PASSWORD_RESET_TOKEN_TTL_SECONDS

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
        "template_type": "forgot_password",
        "content": {
            "link": build_password_reset_link("localhost:3000", token),
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


def test_email_endpoint_returns_generic_response_for_unknown_email() -> None:
    session = FakeStudentSession()
    redis = FakeRedis()

    with student_client(session, redis) as client:
        response = client.post(
            EMAIL_PATH,
            json={"email": "missing@example.com"},
        )

    assert response.status_code == 200
    assert response.json() == {"message": PASSWORD_RESET_SENT}
    assert session.added == []
    assert session.commits == 0
    assert redis.values == {}


def test_email_endpoint_redis_failure_returns_503() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()
    redis.fail("set")

    with student_client(session, redis) as client:
        response = client.post(
            EMAIL_PATH,
            json={"email": DEFAULT_EMAIL},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": PASSWORD_RESET_UNAVAILABLE}
    assert session.added == []
    assert session.rollbacks == 1
    assert redis.values == {}


def test_email_endpoint_cleans_up_token_when_commit_fails() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()

    with student_client(session, redis) as client:
        with pytest.raises(RuntimeError):
            client.post(EMAIL_PATH, json={"email": DEFAULT_EMAIL})

    assert redis.values == {}
    assert session.rollbacks == 1


def test_email_endpoint_rejects_extra_fields() -> None:
    session = FakeStudentSession(make_student())
    redis = FakeRedis()

    with student_client(session, redis) as client:
        response = client.post(
            EMAIL_PATH,
            json={"email": DEFAULT_EMAIL, "role": "student"},
        )

    assert response.status_code == 422
    assert session.added == []
    assert redis.values == {}


def test_password_reset_updates_password_and_verifies_email() -> None:
    student = make_student(is_email_verified=False)
    session = FakeStudentSession(student)
    redis = FakeRedis()
    redis.seed(TOKEN, str(student.user_id), ttl=120)

    with student_client(session, redis) as client:
        response = client.patch(
            RESET_PATH,
            json={
                "password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
                "token": f"  {TOKEN}  ",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"message": "Password reset successfully"}
    assert student.password_hash is not None
    assert verify_hash(NEW_PASSWORD, student.password_hash)
    assert not verify_hash(DEFAULT_PASSWORD, student.password_hash)
    assert student.is_email_verified is True
    assert session.commits == 1
    assert TOKEN not in redis.values
    assert TOKEN in redis.deleted


@pytest.mark.parametrize(
    "password",
    [
        "Ab1!",  # Too short.
        "lowercase1!",  # Missing uppercase.
        "UPPERCASE1!",  # Missing lowercase.
        "NoDigits!!",  # Missing digit.
        "NoSpecial1",  # Missing special character.
        "Has Space1",  # Whitespace is not a special character.
    ],
)
def test_password_reset_rejects_weak_passwords(password: str) -> None:
    student = make_student(is_email_verified=False)
    session = FakeStudentSession(student)
    redis = FakeRedis()
    redis.seed(TOKEN, str(student.user_id), ttl=120)

    with student_client(session, redis) as client:
        response = client.patch(
            RESET_PATH,
            json={
                "password": password,
                "confirm_password": password,
                "token": TOKEN,
            },
        )

    assert response.status_code == 422
    assert session.commits == 0
    assert TOKEN in redis.values


def test_password_reset_requires_matching_confirmation() -> None:
    student = make_student(is_email_verified=False)
    session = FakeStudentSession(student)
    redis = FakeRedis()
    redis.seed(TOKEN, str(student.user_id), ttl=120)

    with student_client(session, redis) as client:
        response = client.patch(
            RESET_PATH,
            json={
                "password": NEW_PASSWORD,
                "confirm_password": "DifferentPass2!",
                "token": TOKEN,
            },
        )

    assert response.status_code == 422
    assert session.commits == 0


def test_password_reset_missing_token_returns_400() -> None:
    session = FakeStudentSession(make_student(is_email_verified=False))
    redis = FakeRedis()

    with student_client(session, redis) as client:
        response = client.patch(
            RESET_PATH,
            json={
                "password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
                "token": TOKEN,
            },
        )

    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_PASSWORD_RESET_TOKEN}
    assert session.commits == 0


def test_password_reset_malformed_user_id_returns_400() -> None:
    session = FakeStudentSession(make_student(is_email_verified=False))
    redis = FakeRedis()
    redis.seed(TOKEN, "not-a-uuid", ttl=120)

    with student_client(session, redis) as client:
        response = client.patch(
            RESET_PATH,
            json={
                "password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
                "token": TOKEN,
            },
        )

    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_PASSWORD_RESET_TOKEN}
    assert session.commits == 0


def test_password_reset_unknown_student_returns_404() -> None:
    session = FakeStudentSession(make_student(is_email_verified=False))
    redis = FakeRedis()
    redis.seed(TOKEN, str(uuid4()), ttl=120)

    with student_client(session, redis) as client:
        response = client.patch(
            RESET_PATH,
            json={
                "password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
                "token": TOKEN,
            },
        )

    assert response.status_code == 404
    assert response.json() == {"detail": STUDENT_NOT_FOUND}
    assert session.commits == 0


def test_password_reset_redis_failure_returns_503() -> None:
    student = make_student(is_email_verified=False)
    session = FakeStudentSession(student)
    redis = FakeRedis()
    redis.seed(TOKEN, str(student.user_id), ttl=120)
    redis.fail("get")

    with student_client(session, redis) as client:
        response = client.patch(
            RESET_PATH,
            json={
                "password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
                "token": TOKEN,
            },
        )

    assert response.status_code == 503
    assert response.json() == {"detail": PASSWORD_RESET_UNAVAILABLE}
    assert session.commits == 0


def test_password_reset_rejects_extra_fields() -> None:
    session = FakeStudentSession(make_student(is_email_verified=False))
    redis = FakeRedis()

    with student_client(session, redis) as client:
        response = client.patch(
            RESET_PATH,
            json={
                "password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
                "token": TOKEN,
                "email": DEFAULT_EMAIL,
            },
        )

    assert response.status_code == 422
    assert session.commits == 0


def test_password_reset_keeps_token_when_commit_fails() -> None:
    student = make_student(is_email_verified=False)
    session = FakeStudentSession(student)
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()
    redis.seed(TOKEN, str(student.user_id), ttl=120)

    with student_client(session, redis) as client:
        with pytest.raises(RuntimeError):
            client.patch(
                RESET_PATH,
                json={
                    "password": NEW_PASSWORD,
                    "confirm_password": NEW_PASSWORD,
                    "token": TOKEN,
                },
            )

    assert session.rollbacks == 1
    assert TOKEN in redis.values
