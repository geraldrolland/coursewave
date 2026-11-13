"""Tests for instructor Google authentication."""

import json
import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest

import app.services.instructor.router.instructor as instructor_router
from app.security import unsign_cookie_value, verify_hash
from app.services.instructor.google import GoogleUserInfo
from app.services.instructor.model import AuthMethod, Instructor
from app.services.instructor.utils import (
    GOOGLE_AUTH_FAILED_MSG,
    GOOGLE_AUTH_SUCCESS_PATH,
    OTP_CODE_TTL_SECONDS,
    OTP_TOKEN_TTL_SECONDS,
    build_otp_verification_link,
)
from app.services.notification.model import (
    Notification,
    NotificationAudience,
    NotificationType,
)
from app.services.outbox.model import Outbox
from tests.fakes import FakeRedis
from tests.helpers import TEST_COOKIE_SECRET, TEST_JWT_SECRET
from tests.instructor_fakes import (
    DEFAULT_EMAIL,
    FakeInstructorSession,
    instructor_client,
    make_instructor,
)

GOOGLE_PATH = "/instructor/api/v1/google"
REFRESH_PATH = "/auth/api/v1/token-refresh"


def google_failure_url(state: str = "login") -> str:
    """Build the expected Google failure redirect URL."""

    return f"http://localhost:3000/{state}?msg={GOOGLE_AUTH_FAILED_MSG}"


def google_user_info(**overrides: object) -> GoogleUserInfo:
    """Build a Google userinfo payload for endpoint tests."""

    payload: dict[str, object] = {
        "email": DEFAULT_EMAIL,
        "email_verified": True,
        "given_name": "Ada",
        "family_name": "Lovelace",
        "name": "Ada Lovelace",
        "picture": "https://example.com/ada.png",
    }
    payload.update(overrides)
    return GoogleUserInfo.model_validate(payload)


def patch_google_flow(
    monkeypatch: pytest.MonkeyPatch,
    *,
    user_info: GoogleUserInfo | None = None,
    exchange_error: Exception | None = None,
    userinfo_error: Exception | None = None,
) -> None:
    """Replace the instructor Google HTTP helpers with fakes."""

    async def fake_exchange(_: str, __: object) -> str:
        if exchange_error is not None:
            raise exchange_error
        return "google-access-token"

    async def fake_userinfo(_: str) -> GoogleUserInfo:
        if userinfo_error is not None:
            raise userinfo_error
        return user_info or google_user_info()

    monkeypatch.setattr(
        instructor_router,
        "exchange_code_for_access_token",
        fake_exchange,
    )
    monkeypatch.setattr(
        instructor_router,
        "fetch_google_user_info",
        fake_userinfo,
    )


def test_google_auth_logs_in_existing_verified_instructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instructor = make_instructor()
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    patch_google_flow(monkeypatch)

    with instructor_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": " auth-code ", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == (
        f"http://localhost:3000{GOOGLE_AUTH_SUCCESS_PATH}"
    )
    assert session.commits == 0
    assert session.added == []

    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith("refresh=")
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=Strict" in set_cookie
    assert "Path=/auth/api/v1/token-refresh" in set_cookie
    assert "Max-Age=604800" in set_cookie

    cookie_value = set_cookie.split(";", 1)[0].split("=", 1)[1]
    refresh_token = unsign_cookie_value(cookie_value, TEST_COOKIE_SECRET)
    assert refresh_token is not None
    payload = jwt.decode(
        refresh_token,
        TEST_JWT_SECRET,
        algorithms=["HS256"],
    )
    assert payload["type"] == "refresh"
    session_key = f"session:{payload['sessionId']}"
    assert redis.values[session_key] == json.dumps(
        {
            "id": str(instructor.instructor_id),
            "email": DEFAULT_EMAIL,
            "role": "instructor",
        }
    )
    assert redis.ttls[session_key] == 604800

    cookie_header = set_cookie.split(";", 1)[0]
    with instructor_client(session, redis) as client:
        refresh_response = client.post(
            REFRESH_PATH,
            headers={"Cookie": cookie_header},
        )

    assert refresh_response.status_code == 200
    assert refresh_response.json()["accessToken"]


def test_google_auth_verifies_existing_unverified_instructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instructor = make_instructor(is_email_verified=False)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    patch_google_flow(monkeypatch)

    with instructor_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].endswith(GOOGLE_AUTH_SUCCESS_PATH)
    assert instructor.is_email_verified is True
    assert instructor.auth_method is AuthMethod.EMAIL
    assert session.commits == 1
    assert session.added == []


def test_google_auth_creates_google_instructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()
    patch_google_flow(monkeypatch)

    with instructor_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].endswith(GOOGLE_AUTH_SUCCESS_PATH)
    instructors = [
        model for model in session.added if isinstance(model, Instructor)
    ]
    assert len(instructors) == 1
    instructor = instructors[0]
    assert instructor.email == DEFAULT_EMAIL
    assert instructor.role == "instructor"
    assert instructor.is_email_verified is True
    assert instructor.auth_method is AuthMethod.GOOGLE
    assert instructor.password_hash is None
    assert session.commits == 1


def test_google_auth_2fa_redirects_to_otp_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.instructor.utils.secrets.randbelow",
        lambda _: 42,
    )
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    patch_google_flow(monkeypatch)
    before = int(time.time())

    with instructor_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code", "state": "login"},
            follow_redirects=False,
        )

    after = int(time.time())

    assert response.status_code == 302
    assert response.headers.get("set-cookie") is None
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.scheme == "http"
    assert parsed.netloc == "localhost:3000"
    assert parsed.path == "/otp-verification"
    token = parse_qs(parsed.query)["token"][0]
    assert location == build_otp_verification_link(
        "localhost:3000",
        token,
    )

    assert session.commits == 1
    assert redis.ttls[token] == OTP_TOKEN_TTL_SECONDS
    challenge = json.loads(redis.values[token])
    assert set(challenge) == {"instructor_id", "code_hash", "exp"}
    assert challenge["instructor_id"] == str(instructor.instructor_id)
    assert verify_hash("000042", challenge["code_hash"])
    assert not verify_hash("000043", challenge["code_hash"])
    assert (
        before + OTP_CODE_TTL_SECONDS
        <= challenge["exp"]
        <= after + OTP_CODE_TTL_SECONDS
    )
    assert not any(key.startswith("session:") for key in redis.values)

    notifications = [
        model for model in session.added if isinstance(model, Notification)
    ]
    outboxes = [model for model in session.added if isinstance(model, Outbox)]
    assert len(notifications) == 1
    assert len(outboxes) == 1
    notification = notifications[0]
    assert notification.type is NotificationType.EMAIL
    assert notification.belongs_to is NotificationAudience.INSTRUCTOR
    assert notification.user_id == instructor.instructor_id
    assert notification.payload == {
        "template_type": "otp",
        "content": {"otp_code": "000042"},
        "to_email": DEFAULT_EMAIL,
    }


def test_google_auth_missing_code_redirects_to_state() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == google_failure_url()
    assert session.added == []
    assert session.commits == 0
    assert redis.values == {}


def test_google_auth_blank_code_redirects_to_state() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "   ", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == google_failure_url()
    assert redis.values == {}


def test_google_auth_missing_state_returns_422() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code"},
            follow_redirects=False,
        )

    assert response.status_code == 422
    assert session.added == []
    assert redis.values == {}


def test_google_auth_failure_redirect_uses_state_path() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"state": "auth/callback"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == google_failure_url("auth/callback")


def test_google_auth_exchange_failure_redirects_to_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeInstructorSession(make_instructor())
    redis = FakeRedis()
    patch_google_flow(
        monkeypatch,
        exchange_error=RuntimeError("google unavailable"),
    )

    with instructor_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == google_failure_url()
    assert session.rollbacks == 1
    assert redis.values == {}


def test_google_auth_userinfo_failure_redirects_to_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeInstructorSession(make_instructor())
    redis = FakeRedis()
    patch_google_flow(
        monkeypatch,
        userinfo_error=RuntimeError("google unavailable"),
    )

    with instructor_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == google_failure_url()
    assert session.rollbacks == 1
    assert redis.values == {}


def test_google_auth_commit_failure_redirects_to_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeInstructorSession()
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()
    patch_google_flow(monkeypatch)

    with instructor_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == google_failure_url()
    assert session.rollbacks == 1
    assert redis.values == {}


def test_google_auth_redis_failure_redirects_to_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeInstructorSession(make_instructor())
    redis = FakeRedis()
    redis.fail("set")
    patch_google_flow(monkeypatch)

    with instructor_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == google_failure_url()
    assert session.rollbacks == 1
    assert redis.values == {}
