"""Tests for refresh-token middleware."""

from urllib.parse import quote

import jwt
from fastapi.testclient import TestClient

from app.middleware.refresh_token import (
    INVALID_REFRESH_PAYLOAD,
    INVALID_REFRESH_TOKEN,
    INVALID_TOKEN_TYPE,
    MISSING_REFRESH_TOKEN,
    REFRESH_FAILED,
    SESSION_NOT_FOUND,
)
from app.security import sign_cookie_value, unsign_cookie_value
from tests.fakes import FakeRedis
from tests.helpers import (
    TEST_COOKIE_SECRET,
    TEST_JWT_SECRET,
    TEST_SESSION_ID,
    make_token,
    refresh_cookie_header,
    refresh_token,
    seed_session,
)

REFRESH_PATH = "/auth/api/v1/token-refresh"


def cookie_header_for(token: str, secret: str = TEST_COOKIE_SECRET) -> str:
    """Build a Cookie header for an arbitrary signed refresh token."""

    signed = sign_cookie_value(token, secret)
    return f"refresh={quote(signed, safe='')}"


def test_missing_refresh_cookie_returns_401(client: TestClient) -> None:
    response = client.post(REFRESH_PATH)

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_REFRESH_TOKEN}


def test_unsigned_cookie_returns_401(client: TestClient) -> None:
    response = client.post(
        REFRESH_PATH,
        headers={"Cookie": f"refresh={refresh_token()}"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_REFRESH_TOKEN}


def test_tampered_cookie_returns_401(client: TestClient) -> None:
    header = refresh_cookie_header(
        cookie_secret="wrong-cookie-secret-that-is-long-enough-123456"
    )

    response = client.post(REFRESH_PATH, headers={"Cookie": header})

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_REFRESH_TOKEN}


def test_access_token_in_refresh_cookie_returns_401(
    client: TestClient,
) -> None:
    token = make_token(token_type="access")

    response = client.post(
        REFRESH_PATH,
        headers={"Cookie": cookie_header_for(token)},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_TOKEN_TYPE}


def test_missing_session_id_returns_401(client: TestClient) -> None:
    token = make_token(session_id=None, token_type="refresh")

    response = client.post(
        REFRESH_PATH,
        headers={"Cookie": cookie_header_for(token)},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_REFRESH_PAYLOAD}


def test_expired_refresh_token_returns_401(client: TestClient) -> None:
    token = refresh_token(expires_seconds=-10)

    response = client.post(
        REFRESH_PATH,
        headers={"Cookie": cookie_header_for(token)},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_REFRESH_TOKEN}


def test_token_with_wrong_jwt_secret_returns_401(client: TestClient) -> None:
    token = refresh_token(secret="wrong-jwt-secret-that-is-long-enough-123456")

    response = client.post(
        REFRESH_PATH,
        headers={"Cookie": cookie_header_for(token)},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_REFRESH_TOKEN}


def test_missing_session_returns_401(client: TestClient) -> None:
    response = client.post(
        REFRESH_PATH,
        headers={"Cookie": refresh_cookie_header()},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": SESSION_NOT_FOUND}


def test_redis_failure_returns_500(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    seed_session(fake_redis)
    fake_redis.fail("get")

    response = client.post(
        REFRESH_PATH,
        headers={"Cookie": refresh_cookie_header()},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": REFRESH_FAILED}


def test_success_rotates_tokens_and_extends_session(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    seed_session(fake_redis, ttl=60)

    response = client.post(
        REFRESH_PATH,
        headers={"Cookie": refresh_cookie_header()},
    )

    assert response.status_code == 200
    access_token = response.json()["accessToken"]
    access_payload = jwt.decode(
        access_token,
        TEST_JWT_SECRET,
        algorithms=["HS256"],
    )
    assert access_payload["type"] == "access"
    assert access_payload["sessionId"] == TEST_SESSION_ID

    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith("refresh=")
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=Strict" in set_cookie
    assert f"Path={REFRESH_PATH}" in set_cookie
    assert "Max-Age=604800" in set_cookie

    cookie_value = set_cookie.split(";", 1)[0].split("=", 1)[1]
    new_refresh = unsign_cookie_value(cookie_value, TEST_COOKIE_SECRET)
    assert new_refresh is not None

    refresh_payload = jwt.decode(
        new_refresh,
        TEST_JWT_SECRET,
        algorithms=["HS256"],
    )
    assert refresh_payload["type"] == "refresh"
    assert refresh_payload["sessionId"] == TEST_SESSION_ID

    assert fake_redis.ttls[f"session:{TEST_SESSION_ID}"] == 604800


def test_trailing_slash_is_accepted(client: TestClient) -> None:
    response = client.post(
        f"{REFRESH_PATH}/",
        headers={"Cookie": refresh_cookie_header()},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": SESSION_NOT_FOUND}


def test_other_paths_fall_through(client: TestClient) -> None:
    response = client.post("/health")

    assert response.status_code == 405


def test_cookie_signed_with_expected_value_round_trips() -> None:
    token = refresh_token()
    signed = sign_cookie_value(token, TEST_COOKIE_SECRET)

    assert unsign_cookie_value(signed, TEST_COOKIE_SECRET) == token
