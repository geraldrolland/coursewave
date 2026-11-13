"""Tests for the protected logout endpoint."""

import json

from fastapi.testclient import TestClient

from app.middleware.auth import (
    INVALID_TOKEN,
    MISSING_AUTHORIZATION,
)
from app.security import REFRESH_COOKIE_NAME, REFRESH_PATH
from app.services.student.router import LOGOUT_UNAVAILABLE
from tests.fakes import FakeRedis
from tests.helpers import (
    TEST_SESSION_ID,
    TEST_USER,
    access_token,
    auth_headers,
    make_token,
    seed_session,
)

LOGOUT_PATH = "/student/api/v1/logout"
SESSION_KEY = f"session:{TEST_SESSION_ID}"


def test_logout_deletes_session_and_clears_refresh_cookie(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    seed_session(fake_redis)

    response = client.post(
        LOGOUT_PATH,
        headers=auth_headers(access_token()),
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Logged out successfully"}
    assert SESSION_KEY not in fake_redis.values
    assert SESSION_KEY in fake_redis.deleted

    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{REFRESH_COOKIE_NAME}=;")
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=Strict" in set_cookie
    assert f"Path={REFRESH_PATH}" in set_cookie
    assert "Max-Age=0" in set_cookie
    assert "Expires=Thu, 01 Jan 1970 00:00:00 GMT" in set_cookie


def test_logout_only_deletes_the_access_token_session(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    seed_session(fake_redis)
    fake_redis.seed(
        "session:other-session",
        json.dumps(TEST_USER),
        ttl=604800,
    )

    response = client.post(
        LOGOUT_PATH,
        headers=auth_headers(access_token()),
    )

    assert response.status_code == 200
    assert SESSION_KEY not in fake_redis.values
    assert fake_redis.values["session:other-session"] == json.dumps(TEST_USER)
    assert fake_redis.ttls["session:other-session"] == 604800


def test_logout_requires_authentication(client: TestClient) -> None:
    response = client.post(LOGOUT_PATH)

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_AUTHORIZATION}
    assert "set-cookie" not in response.headers


def test_logout_rejects_invalid_token(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    seed_session(fake_redis)
    token = make_token(
        secret="wrong-jwt-secret-that-is-long-enough-123456",
    )

    response = client.post(
        LOGOUT_PATH,
        headers=auth_headers(token),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_TOKEN}
    assert SESSION_KEY in fake_redis.values
    assert "set-cookie" not in response.headers


def test_logout_redis_failure_returns_503(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    seed_session(fake_redis)
    fake_redis.fail("delete")

    response = client.post(
        LOGOUT_PATH,
        headers=auth_headers(access_token()),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": LOGOUT_UNAVAILABLE}
    assert SESSION_KEY in fake_redis.values
    assert "set-cookie" not in response.headers
