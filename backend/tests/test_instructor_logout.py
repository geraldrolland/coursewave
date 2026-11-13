"""Tests for the protected instructor logout endpoint."""

import json

from app.middleware.auth import (
    INVALID_TOKEN,
    MISSING_AUTHORIZATION,
)
from app.security import REFRESH_COOKIE_NAME, REFRESH_PATH
from app.services.instructor.utils import LOGOUT_UNAVAILABLE
from tests.fakes import FakeRedis
from tests.helpers import (
    TEST_SESSION_ID,
    access_token,
    auth_headers,
    make_token,
    seed_session,
)
from tests.instructor_fakes import FakeInstructorSession, instructor_client

LOGOUT_PATH = "/instructor/api/v1/logout"
SESSION_KEY = f"session:{TEST_SESSION_ID}"
INSTRUCTOR_USER = {
    "id": "instructor-user-123",
    "email": "ada@example.com",
    "role": "instructor",
}


def test_logout_deletes_session_and_clears_refresh_cookie() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()
    seed_session(redis, user=INSTRUCTOR_USER)

    with instructor_client(session, redis) as client:
        response = client.post(
            LOGOUT_PATH,
            headers=auth_headers(access_token()),
        )

    assert response.status_code == 200
    assert response.json() == {"message": "Logged out successfully"}
    assert SESSION_KEY not in redis.values
    assert SESSION_KEY in redis.deleted

    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{REFRESH_COOKIE_NAME}=;")
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=Strict" in set_cookie
    assert f"Path={REFRESH_PATH}" in set_cookie
    assert "Max-Age=0" in set_cookie
    assert "Expires=Thu, 01 Jan 1970 00:00:00 GMT" in set_cookie


def test_logout_only_deletes_the_access_token_session() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()
    seed_session(redis, user=INSTRUCTOR_USER)
    redis.seed(
        "session:other-session",
        json.dumps(INSTRUCTOR_USER),
        ttl=604800,
    )

    with instructor_client(session, redis) as client:
        response = client.post(
            LOGOUT_PATH,
            headers=auth_headers(access_token()),
        )

    assert response.status_code == 200
    assert SESSION_KEY not in redis.values
    assert redis.values["session:other-session"] == json.dumps(
        INSTRUCTOR_USER
    )
    assert redis.ttls["session:other-session"] == 604800


def test_logout_requires_authentication() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.post(LOGOUT_PATH)

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_AUTHORIZATION}
    assert "set-cookie" not in response.headers


def test_logout_rejects_invalid_token() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()
    seed_session(redis, user=INSTRUCTOR_USER)
    token = make_token(
        secret="wrong-jwt-secret-that-is-long-enough-123456",
    )

    with instructor_client(session, redis) as client:
        response = client.post(
            LOGOUT_PATH,
            headers=auth_headers(token),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_TOKEN}
    assert SESSION_KEY in redis.values
    assert "set-cookie" not in response.headers


def test_logout_redis_failure_returns_503() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()
    seed_session(redis, user=INSTRUCTOR_USER)
    redis.fail("delete")

    with instructor_client(session, redis) as client:
        response = client.post(
            LOGOUT_PATH,
            headers=auth_headers(access_token()),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": LOGOUT_UNAVAILABLE}
    assert SESSION_KEY in redis.values
    assert "set-cookie" not in response.headers
