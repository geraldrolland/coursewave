"""Tests for instructor email verification."""

from uuid import uuid4

from redis.exceptions import RedisError

from app.services.instructor.utils import (
    EMAIL_VERIFICATION_UNAVAILABLE,
    INSTRUCTOR_NOT_FOUND,
    INVALID_VERIFICATION_TOKEN,
)
from tests.fakes import FakeRedis
from tests.instructor_fakes import (
    FakeInstructorSession,
    instructor_client,
    make_instructor,
)

VERIFY_PATH = "/instructor/api/v1/verify-email"
TOKEN = "verification-token"


def test_verify_email_marks_instructor_verified_and_deletes_token() -> None:
    instructor = make_instructor(is_email_verified=False)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    redis.seed(TOKEN, str(instructor.instructor_id), ttl=120)

    with instructor_client(session, redis) as client:
        response = client.patch(
            VERIFY_PATH,
            json={"token": f"  {TOKEN}  "},
        )

    assert response.status_code == 200
    assert response.json() == {"message": "Email verified successfully"}
    assert instructor.is_email_verified is True
    assert session.commits == 1
    assert TOKEN not in redis.values
    assert TOKEN in redis.deleted


def test_verify_email_is_idempotent_when_already_verified() -> None:
    instructor = make_instructor(is_email_verified=True)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    redis.seed(TOKEN, str(instructor.instructor_id), ttl=120)

    with instructor_client(session, redis) as client:
        response = client.patch(VERIFY_PATH, json={"token": TOKEN})

    assert response.status_code == 200
    assert response.json() == {"message": "Email verified successfully"}
    assert instructor.is_email_verified is True
    assert session.commits == 0
    assert TOKEN not in redis.values


def test_verify_email_missing_token_returns_400() -> None:
    session = FakeInstructorSession(make_instructor(is_email_verified=False))
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.patch(VERIFY_PATH, json={"token": TOKEN})

    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_VERIFICATION_TOKEN}
    assert session.commits == 0


def test_verify_email_malformed_instructor_id_returns_400() -> None:
    session = FakeInstructorSession(make_instructor(is_email_verified=False))
    redis = FakeRedis()
    redis.seed(TOKEN, "not-a-uuid", ttl=120)

    with instructor_client(session, redis) as client:
        response = client.patch(VERIFY_PATH, json={"token": TOKEN})

    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_VERIFICATION_TOKEN}
    assert session.commits == 0


def test_verify_email_unknown_instructor_returns_404() -> None:
    session = FakeInstructorSession(make_instructor(is_email_verified=False))
    redis = FakeRedis()
    redis.seed(TOKEN, str(uuid4()), ttl=120)

    with instructor_client(session, redis) as client:
        response = client.patch(VERIFY_PATH, json={"token": TOKEN})

    assert response.status_code == 404
    assert response.json() == {"detail": INSTRUCTOR_NOT_FOUND}
    assert session.commits == 0


def test_verify_email_redis_failure_returns_503() -> None:
    instructor = make_instructor(is_email_verified=False)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    redis.seed(TOKEN, str(instructor.instructor_id), ttl=120)
    redis.fail("get", RedisError("redis unavailable"))

    with instructor_client(session, redis) as client:
        response = client.patch(VERIFY_PATH, json={"token": TOKEN})

    assert response.status_code == 503
    assert response.json() == {"detail": EMAIL_VERIFICATION_UNAVAILABLE}
    assert instructor.is_email_verified is False
    assert session.commits == 0


def test_verify_email_rejects_extra_fields() -> None:
    session = FakeInstructorSession(make_instructor(is_email_verified=False))
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.patch(
            VERIFY_PATH,
            json={"token": TOKEN, "instructor_id": str(uuid4())},
        )

    assert response.status_code == 422
    assert session.commits == 0


def test_verify_email_rolls_back_when_commit_fails() -> None:
    instructor = make_instructor(is_email_verified=False)
    session = FakeInstructorSession(instructor)
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()
    redis.seed(TOKEN, str(instructor.instructor_id), ttl=120)

    with instructor_client(session, redis) as client:
        response = client.patch(VERIFY_PATH, json={"token": TOKEN})

    assert response.status_code == 500
    assert instructor.is_email_verified is True
    assert session.rollbacks == 1
    assert TOKEN in redis.values
