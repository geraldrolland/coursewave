"""Tests for the protected current-session endpoint."""

import pytest
from fastapi.testclient import TestClient

from app.middleware.auth import (
    INVALID_TOKEN,
    MISSING_AUTHORIZATION,
    SESSION_NOT_FOUND,
)
from app.services.student.router import INVALID_AUTH_SESSION
from tests.fakes import FakeRedis
from tests.helpers import (
    TEST_USER,
    TEST_USER_ID,
    access_token,
    auth_headers,
    make_token,
    seed_session,
)

ME_PATH = "/student/api/v1/me"


def test_me_returns_session_identity(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    seed_session(fake_redis)

    response = client.get(ME_PATH, headers=auth_headers(access_token()))

    assert response.status_code == 200
    assert response.json() == {
        "id": TEST_USER_ID,
        "email": TEST_USER["email"],
    }


def test_me_requires_authentication(client: TestClient) -> None:
    response = client.get(ME_PATH)

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_AUTHORIZATION}


def test_me_rejects_invalid_token(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    seed_session(fake_redis)
    token = make_token(
        secret="wrong-jwt-secret-that-is-long-enough-123456",
    )

    response = client.get(ME_PATH, headers=auth_headers(token))

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_TOKEN}


def test_me_rejects_missing_session(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    response = client.get(ME_PATH, headers=auth_headers(access_token()))

    assert response.status_code == 401
    assert response.json() == {"detail": SESSION_NOT_FOUND}
    assert fake_redis.values == {}


@pytest.mark.parametrize(
    "user",
    [
        {"role": "student", "email": "ada@example.com"},
        {"role": "student", "email": "ada@example.com"},
        {"id": TEST_USER_ID, "role": "student"},
        {"id": "", "email": "ada@example.com"},
        {"id": 123, "email": "ada@example.com"},
        {"userId": TEST_USER_ID, "email": "ada@example.com"},
    ],
)
def test_me_rejects_incomplete_session(
    client: TestClient,
    fake_redis: FakeRedis,
    user: dict[str, object],
) -> None:
    seed_session(fake_redis, user=user)

    response = client.get(ME_PATH, headers=auth_headers(access_token()))

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_AUTH_SESSION}
