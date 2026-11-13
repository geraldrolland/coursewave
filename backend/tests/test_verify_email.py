"""Tests for the student email verification endpoint."""

from uuid import uuid4

import pytest

from app.services.student.router import (
    EMAIL_VERIFICATION_UNAVAILABLE,
    INVALID_VERIFICATION_TOKEN,
    STUDENT_NOT_FOUND,
)
from tests.fakes import FakeRedis
from tests.student_fakes import (
    FakeStudentSession,
    make_student,
    student_client,
)

VERIFY_PATH = "/student/api/v1/verify-email"
TOKEN = "verification-token"


def test_verify_email_marks_student_verified_and_deletes_token() -> None:
    student = make_student(is_email_verified=False)
    session = FakeStudentSession(student)
    redis = FakeRedis()
    redis.seed(TOKEN, str(student.user_id), ttl=120)

    with student_client(session, redis) as client:
        response = client.patch(
            VERIFY_PATH,
            json={"token": f"  {TOKEN}  "},
        )

    assert response.status_code == 200
    assert response.json() == {"message": "Email verified successfully"}
    assert student.is_email_verified is True
    assert session.commits == 1
    assert TOKEN not in redis.values
    assert TOKEN in redis.deleted


def test_verify_email_is_idempotent_when_already_verified() -> None:
    student = make_student(is_email_verified=True)
    session = FakeStudentSession(student)
    redis = FakeRedis()
    redis.seed(TOKEN, str(student.user_id), ttl=120)

    with student_client(session, redis) as client:
        response = client.patch(VERIFY_PATH, json={"token": TOKEN})

    assert response.status_code == 200
    assert response.json() == {"message": "Email verified successfully"}
    assert student.is_email_verified is True
    assert session.commits == 0
    assert TOKEN not in redis.values


def test_verify_email_missing_token_returns_400() -> None:
    session = FakeStudentSession(make_student(is_email_verified=False))
    redis = FakeRedis()

    with student_client(session, redis) as client:
        response = client.patch(VERIFY_PATH, json={"token": TOKEN})

    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_VERIFICATION_TOKEN}
    assert session.commits == 0


def test_verify_email_malformed_user_id_returns_400() -> None:
    session = FakeStudentSession(make_student(is_email_verified=False))
    redis = FakeRedis()
    redis.seed(TOKEN, "not-a-uuid", ttl=120)

    with student_client(session, redis) as client:
        response = client.patch(VERIFY_PATH, json={"token": TOKEN})

    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_VERIFICATION_TOKEN}
    assert session.commits == 0


def test_verify_email_unknown_student_returns_404() -> None:
    session = FakeStudentSession(make_student(is_email_verified=False))
    redis = FakeRedis()
    redis.seed(TOKEN, str(uuid4()), ttl=120)

    with student_client(session, redis) as client:
        response = client.patch(VERIFY_PATH, json={"token": TOKEN})

    assert response.status_code == 404
    assert response.json() == {"detail": STUDENT_NOT_FOUND}
    assert session.commits == 0


def test_verify_email_redis_failure_returns_503() -> None:
    student = make_student(is_email_verified=False)
    session = FakeStudentSession(student)
    redis = FakeRedis()
    redis.seed(TOKEN, str(student.user_id), ttl=120)
    redis.fail("get")

    with student_client(session, redis) as client:
        response = client.patch(VERIFY_PATH, json={"token": TOKEN})

    assert response.status_code == 503
    assert response.json() == {"detail": EMAIL_VERIFICATION_UNAVAILABLE}
    assert student.is_email_verified is False
    assert session.commits == 0


def test_verify_email_rejects_extra_fields() -> None:
    session = FakeStudentSession(make_student(is_email_verified=False))
    redis = FakeRedis()

    with student_client(session, redis) as client:
        response = client.patch(
            VERIFY_PATH,
            json={"token": TOKEN, "user_id": str(uuid4())},
        )

    assert response.status_code == 422
    assert session.commits == 0


def test_verify_email_rolls_back_when_commit_fails() -> None:
    student = make_student(is_email_verified=False)
    session = FakeStudentSession(student)
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()
    redis.seed(TOKEN, str(student.user_id), ttl=120)

    with student_client(session, redis) as client:
        with pytest.raises(RuntimeError):
            client.patch(VERIFY_PATH, json={"token": TOKEN})

    assert student.is_email_verified is True
    assert session.rollbacks == 1
    assert TOKEN in redis.values
