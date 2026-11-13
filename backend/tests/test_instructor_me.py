"""Tests for the protected instructor current-session endpoint."""

import pytest

from app.middleware.auth import (
    INVALID_TOKEN,
    MISSING_AUTHORIZATION,
    SESSION_NOT_FOUND,
)
from app.services.instructor.utils import INVALID_AUTH_SESSION
from tests.fakes import FakeRedis
from tests.helpers import (
    TEST_USER_ID,
    access_token,
    auth_headers,
    make_token,
    seed_session,
)
from tests.instructor_fakes import FakeInstructorSession, instructor_client

ME_PATH = "/instructor/api/v1/me"
INSTRUCTOR_USER = {
    "id": TEST_USER_ID,
    "email": "ada@example.com",
    "role": "instructor",
}


def test_me_returns_instructor_session_identity() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()
    seed_session(redis, user=INSTRUCTOR_USER)

    with instructor_client(session, redis) as client:
        response = client.get(ME_PATH, headers=auth_headers(access_token()))

    assert response.status_code == 200
    assert response.json() == {
        "id": TEST_USER_ID,
        "email": INSTRUCTOR_USER["email"],
    }


def test_me_requires_authentication() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.get(ME_PATH)

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_AUTHORIZATION}


def test_me_rejects_invalid_token() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()
    seed_session(redis, user=INSTRUCTOR_USER)
    token = make_token(
        secret="wrong-jwt-secret-that-is-long-enough-123456",
    )

    with instructor_client(session, redis) as client:
        response = client.get(ME_PATH, headers=auth_headers(token))

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_TOKEN}


def test_me_rejects_missing_session() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.get(ME_PATH, headers=auth_headers(access_token()))

    assert response.status_code == 401
    assert response.json() == {"detail": SESSION_NOT_FOUND}
    assert redis.values == {}


@pytest.mark.parametrize(
    "user",
    [
        {"role": "instructor", "email": "ada@example.com"},
        {"id": TEST_USER_ID, "role": "instructor"},
        {"id": "", "email": "ada@example.com"},
        {"id": 123, "email": "ada@example.com"},
        {"userId": TEST_USER_ID, "email": "ada@example.com"},
    ],
)
def test_me_rejects_incomplete_session(user: dict[str, object]) -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()
    seed_session(redis, user=user)

    with instructor_client(session, redis) as client:
        response = client.get(ME_PATH, headers=auth_headers(access_token()))

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_AUTH_SESSION}
