"""Tests for Google OAuth student authentication."""

import asyncio
import json
from urllib.parse import parse_qs

import httpx
import jwt
import pytest

import app.services.student.router as student_router
from app.base_config import Settings
from app.security import unsign_cookie_value
from app.services.student.google import (
    GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL,
    GoogleAuthError,
    GoogleUserInfo,
)
from app.services.student.model import AuthMethod, Student
from app.services.student.router import GOOGLE_AUTH_SUCCESS_PATH
from app.services.student.utils import (
    GOOGLE_AUTH_FAILED_MSG,
    exchange_code_for_access_token,
    fetch_google_user_info,
)
from tests.fakes import FakeRedis
from tests.helpers import (
    TEST_COOKIE_SECRET,
    TEST_JWT_SECRET,
    build_settings,
)
from tests.student_fakes import (
    DEFAULT_EMAIL,
    FakeStudentSession,
    make_student,
    student_client,
)

GOOGLE_PATH = "/student/api/v1/google"
REFRESH_PATH = "/auth/api/v1/token-refresh"


def google_settings() -> Settings:
    """Build settings containing recognizable Google credentials."""

    return build_settings(
        google_client_id="test-google-client-id",
        google_client_secret="test-google-client-secret",
        google_redirect_uri="http://localhost:3000/student/api/v1/google",
    )


def google_failure_url(state: str = "login") -> str:
    """Build the expected Google failure redirect URL."""

    return f"http://localhost:3000/{state}?msg={GOOGLE_AUTH_FAILED_MSG}"


def test_exchange_code_for_access_token_posts_expected_form() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"access_token": "google-token"})

    async def run() -> str:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await exchange_code_for_access_token(
                "auth-code",
                google_settings(),
                client,
            )

    access_token = asyncio.run(run())

    assert access_token == "google-token"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == GOOGLE_TOKEN_URL
    form = parse_qs(request.content.decode("utf-8"))
    assert form == {
        "code": ["auth-code"],
        "client_id": ["test-google-client-id"],
        "client_secret": ["test-google-client-secret"],
        "redirect_uri": ["http://localhost:3000/student/api/v1/google"],
        "grant_type": ["authorization_code"],
    }


def test_exchange_code_rejects_http_errors() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await exchange_code_for_access_token(
                "expired-code",
                google_settings(),
                client,
            )

    with pytest.raises(GoogleAuthError):
        asyncio.run(run())


def test_exchange_code_requires_access_token() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token_type": "Bearer"})

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await exchange_code_for_access_token(
                "auth-code",
                google_settings(),
                client,
            )

    with pytest.raises(GoogleAuthError, match="access token"):
        asyncio.run(run())


def test_fetch_google_user_info_sends_bearer_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "email": " ADA@EXAMPLE.COM ",
                "verified_email": True,
                "given_name": "Ada",
                "family_name": "Lovelace",
                "name": "Ada Lovelace",
                "picture": "https://example.com/ada.png",
            },
        )

    async def run() -> GoogleUserInfo:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await fetch_google_user_info("google-token", client)

    user_info = asyncio.run(run())

    assert user_info.email == DEFAULT_EMAIL
    assert user_info.email_verified is True
    assert user_info.given_name == "Ada"
    assert user_info.family_name == "Lovelace"
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == GOOGLE_USERINFO_URL
    assert requests[0].headers["authorization"] == "Bearer google-token"


def test_fetch_google_user_info_supports_openid_email_verified() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"email": DEFAULT_EMAIL, "email_verified": True},
        )

    async def run() -> GoogleUserInfo:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await fetch_google_user_info("google-token", client)

    user_info = asyncio.run(run())

    assert user_info.email_verified is True


def test_fetch_google_user_info_rejects_invalid_payload() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "Ada Lovelace"})

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await fetch_google_user_info("google-token", client)

    with pytest.raises(GoogleAuthError, match="invalid"):
        asyncio.run(run())


def test_fetch_google_user_info_rejects_http_errors() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_token"})

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await fetch_google_user_info("google-token", client)

    with pytest.raises(GoogleAuthError, match="userinfo"):
        asyncio.run(run())


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
    """Replace the Google HTTP helpers with deterministic fakes."""

    async def fake_exchange(_: str, __: object) -> str:
        if exchange_error is not None:
            raise exchange_error
        return "google-access-token"

    async def fake_userinfo(_: str) -> GoogleUserInfo:
        if userinfo_error is not None:
            raise userinfo_error
        return user_info or google_user_info()

    monkeypatch.setattr(
        student_router,
        "exchange_code_for_access_token",
        fake_exchange,
    )
    monkeypatch.setattr(
        student_router,
        "fetch_google_user_info",
        fake_userinfo,
    )


def test_google_auth_logs_in_existing_verified_student(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()
    patch_google_flow(monkeypatch)

    with student_client(session, redis) as client:
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
    assert f"Path={REFRESH_PATH}" in set_cookie
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
            "id": str(student.user_id),
            "email": DEFAULT_EMAIL,
            "role": "student",
        }
    )
    assert redis.ttls[session_key] == 604800

    cookie_header = set_cookie.split(";", 1)[0]
    with student_client(session, redis) as client:
        refresh_response = client.post(
            REFRESH_PATH,
            headers={"Cookie": cookie_header},
        )

    assert refresh_response.status_code == 200
    assert refresh_response.json()["accessToken"]


def test_google_auth_verifies_existing_unverified_student(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    student = make_student(is_email_verified=False)
    session = FakeStudentSession(student)
    redis = FakeRedis()
    patch_google_flow(monkeypatch)

    with student_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].endswith(GOOGLE_AUTH_SUCCESS_PATH)
    assert student.is_email_verified is True
    assert student.auth_method is AuthMethod.EMAIL
    assert session.commits == 1
    assert session.added == []


def test_google_auth_creates_google_student(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeStudentSession()
    redis = FakeRedis()
    patch_google_flow(monkeypatch)

    with student_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].endswith(GOOGLE_AUTH_SUCCESS_PATH)
    students = [
        model for model in session.added if isinstance(model, Student)
    ]
    assert len(students) == 1
    student = students[0]
    assert student.first_name == "Ada"
    assert student.last_name == "Lovelace"
    assert student.email == DEFAULT_EMAIL
    assert student.is_email_verified is True
    assert student.auth_method is AuthMethod.GOOGLE
    assert student.password_hash is None
    assert session.commits == 1


def test_google_auth_falls_back_to_name_and_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeStudentSession()
    redis = FakeRedis()
    patch_google_flow(
        monkeypatch,
        user_info=google_user_info(
            email="grace@example.com",
            given_name=None,
            family_name=None,
            name="Grace Brewster Hopper",
        ),
    )

    with student_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    student = next(
        model for model in session.added if isinstance(model, Student)
    )
    assert student.first_name == "Grace"
    assert student.last_name == "Brewster Hopper"


def test_google_auth_uses_email_local_part_when_name_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeStudentSession()
    redis = FakeRedis()
    patch_google_flow(
        monkeypatch,
        user_info=google_user_info(
            email="solo@example.com",
            given_name=None,
            family_name=None,
            name=None,
        ),
    )

    with student_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    student = next(
        model for model in session.added if isinstance(model, Student)
    )
    assert student.first_name == "solo"
    assert student.last_name == "Student"


def test_google_auth_missing_code_redirects_to_login() -> None:
    session = FakeStudentSession()
    redis = FakeRedis()

    with student_client(session, redis) as client:
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


def test_google_auth_missing_state_returns_422() -> None:
    session = FakeStudentSession()
    redis = FakeRedis()

    with student_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code"},
            follow_redirects=False,
        )

    assert response.status_code == 422
    assert session.added == []
    assert session.commits == 0
    assert redis.values == {}


def test_google_auth_failure_redirect_uses_state_path() -> None:
    session = FakeStudentSession()
    redis = FakeRedis()

    with student_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"state": "auth/callback"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == google_failure_url("auth/callback")
    assert session.added == []
    assert session.commits == 0
    assert redis.values == {}


def test_google_auth_blank_code_redirects_to_login() -> None:
    session = FakeStudentSession()
    redis = FakeRedis()

    with student_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "   ", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == google_failure_url()
    assert redis.values == {}


def test_google_auth_token_exchange_failure_redirects_to_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeStudentSession(make_student())
    redis = FakeRedis()
    patch_google_flow(
        monkeypatch,
        exchange_error=RuntimeError("google unavailable"),
    )

    with student_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == google_failure_url()
    assert session.commits == 0
    assert session.rollbacks == 1
    assert redis.values == {}


def test_google_auth_userinfo_failure_redirects_to_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeStudentSession(make_student())
    redis = FakeRedis()
    patch_google_flow(
        monkeypatch,
        userinfo_error=RuntimeError("google unavailable"),
    )

    with student_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == google_failure_url()
    assert session.commits == 0
    assert redis.values == {}


def test_google_auth_commit_failure_redirects_to_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeStudentSession()
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()
    patch_google_flow(monkeypatch)

    with student_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == google_failure_url()
    assert session.rollbacks == 1
    assert redis.values == {}


def test_google_auth_redis_failure_redirects_to_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeStudentSession(make_student())
    redis = FakeRedis()
    redis.fail("set")
    patch_google_flow(monkeypatch)

    with student_client(session, redis) as client:
        response = client.get(
            GOOGLE_PATH,
            params={"code": "auth-code", "state": "login"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == google_failure_url()
    assert session.rollbacks == 1
    assert redis.values == {}
