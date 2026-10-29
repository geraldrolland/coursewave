"""Shared helpers for middleware tests."""

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import jwt

from app.base_config import Settings
from app.security import sign_cookie_value
from tests.fakes import FakeRedis

TEST_JWT_SECRET = "test-jwt-secret-for-coursewave-tests-0123456789"
TEST_COOKIE_SECRET = "test-cookie-secret-for-coursewave-tests-0123456789"
TEST_SESSION_ID = "session-123"
TEST_USER_ID = "user-123"
TEST_USER = {
    "userId": TEST_USER_ID,
    "role": "student",
    "email": "ada@example.com",
}


def build_settings(**overrides: Any) -> Settings:
    """Build settings suitable for isolated middleware tests."""

    values: dict[str, Any] = {
        "jwt_secret": TEST_JWT_SECRET,
        "cookie_secret": TEST_COOKIE_SECRET,
        "log_level": "CRITICAL",
        "auto_create_tables": False,
        "rate_limit_max": 1000,
    }
    values.update(overrides)
    return Settings(**values)


def make_token(
    session_id: str | None = TEST_SESSION_ID,
    token_type: str = "access",
    secret: str = TEST_JWT_SECRET,
    expires_seconds: float = 420,
) -> str:
    """Create an HS256 JWT for tests, including deliberately invalid tokens."""

    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sessionId": session_id,
        "type": token_type,
        "iat": now,
        "exp": now + timedelta(seconds=expires_seconds),
    }
    if session_id is None:
        payload.pop("sessionId")

    return jwt.encode(payload, secret, algorithm="HS256")


def access_token(
    session_id: str = TEST_SESSION_ID,
    secret: str = TEST_JWT_SECRET,
    expires_seconds: float = 420,
) -> str:
    """Create a valid access token for the test session."""

    return make_token(session_id, "access", secret, expires_seconds)


def refresh_token(
    session_id: str = TEST_SESSION_ID,
    secret: str = TEST_JWT_SECRET,
    expires_seconds: float = 604800,
) -> str:
    """Create a valid refresh token for the test session."""

    return make_token(session_id, "refresh", secret, expires_seconds)


def auth_headers(token: str) -> dict[str, str]:
    """Build an Authorization header for a bearer token."""

    return {"Authorization": f"Bearer {token}"}


def seed_session(
    redis: FakeRedis,
    session_id: str = TEST_SESSION_ID,
    user: dict[str, Any] | None = None,
    ttl: int = 604800,
) -> None:
    """Store a gateway-compatible session document in fake Redis."""

    redis.seed(
        f"session:{session_id}",
        json.dumps(user or TEST_USER),
        ttl=ttl,
    )


def signed_refresh_cookie(
    session_id: str = TEST_SESSION_ID,
    token_secret: str = TEST_JWT_SECRET,
    cookie_secret: str = TEST_COOKIE_SECRET,
    expires_seconds: float = 604800,
) -> str:
    """Create an Express-compatible signed refresh cookie value."""

    token = refresh_token(session_id, token_secret, expires_seconds)
    return sign_cookie_value(token, cookie_secret)


def refresh_cookie_header(**kwargs: Any) -> str:
    """Build a URL-encoded ``Cookie`` header carrying the refresh cookie."""

    signed = signed_refresh_cookie(**kwargs)
    return f"refresh={quote(signed, safe='')}"
