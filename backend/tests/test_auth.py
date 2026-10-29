"""Tests for JWT authentication middleware."""

import pytest
from fastapi.testclient import TestClient

from app.middleware.auth import (
    AUTH_UNAVAILABLE,
    INVALID_TOKEN,
    INVALID_TOKEN_PAYLOAD,
    INVALID_TOKEN_TYPE,
    MISSING_AUTHORIZATION,
    SESSION_NOT_FOUND,
)
from tests.fakes import FakeRedis
from tests.helpers import access_token, auth_headers, make_token, seed_session


def test_health_is_excluded_from_authentication(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_refresh_endpoint_is_excluded_from_authentication(
    client: TestClient,
) -> None:
    response = client.post("/auth/api/v1/token-refresh")

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing refresh token"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer "},
    ],
)
def test_missing_or_malformed_authorization_returns_401(
    client: TestClient,
    headers: dict[str, str],
) -> None:
    response = client.get("/openapi.json", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_AUTHORIZATION}


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        make_token(secret="wrong-jwt-secret-that-is-long-enough-123456"),
        make_token(expires_seconds=-10),
    ],
)
def test_invalid_or_expired_token_returns_401(
    client: TestClient,
    token: str,
) -> None:
    response = client.get("/openapi.json", headers=auth_headers(token))

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_TOKEN}


def test_refresh_token_type_is_rejected(client: TestClient) -> None:
    token = make_token(token_type="refresh")

    response = client.get("/openapi.json", headers=auth_headers(token))

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_TOKEN_TYPE}


def test_missing_session_id_returns_401(client: TestClient) -> None:
    token = make_token(session_id=None)

    response = client.get("/openapi.json", headers=auth_headers(token))

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_TOKEN_PAYLOAD}


def test_missing_session_returns_401(client: TestClient) -> None:
    response = client.get(
        "/openapi.json",
        headers=auth_headers(access_token()),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": SESSION_NOT_FOUND}


def test_valid_token_and_session_allow_request(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    seed_session(fake_redis)

    response = client.get(
        "/openapi.json",
        headers=auth_headers(access_token()),
    )

    assert response.status_code == 200
    assert "openapi" in response.json()


def test_redis_failure_returns_500(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    seed_session(fake_redis)
    fake_redis.fail("get")

    response = client.get(
        "/openapi.json",
        headers=auth_headers(access_token()),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": AUTH_UNAVAILABLE}
