"""Tests for the student registration endpoint."""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError

from app.base_config import Settings
from app.base_dependencies import get_session
from app.main import create_app
from app.security import verify_password
from app.services.student.model import Outbox, OutboxStatus, Student
from app.services.student.router.student import (
    OUTBOX_TOPIC,
    VERIFICATION_TOKEN_TTL_SECONDS,
    build_verification_link,
)
from tests.helpers import TEST_PROXY_SECRET, signed_headers

REGISTER_PATH = "/student/api/v1/register"

VALID_PAYLOAD = {
    "first_name": "Ada",
    "middle_name": "Augusta",
    "last_name": "Lovelace",
    "email": "ada@example.com",
    "password": "StrongPass1!",
    "confirm_password": "StrongPass1!",
}


class FakeTransaction:
    """Minimal async transaction context manager for a fake session."""

    def __init__(self, session: "FakeSession") -> None:
        self.session = session

    async def __aenter__(self) -> "FakeTransaction":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> bool:
        if exc_type is None:
            self.session.committed = True
        else:
            self.session.rolled_back = True

        return False


class FakeSession:
    """Fake SQLModel async session used to observe transaction behavior."""

    def __init__(self, flush_error: Exception | None = None) -> None:
        self.added: list[Any] = []
        self.flush_error = flush_error
        self.flushed = False
        self.committed = False
        self.rolled_back = False

    def add(self, model: Any) -> None:
        self.added.append(model)

    async def flush(self) -> None:
        self.flushed = True
        if self.flush_error is not None:
            raise self.flush_error

    def begin(self) -> FakeTransaction:
        return FakeTransaction(self)


class FakeRedis:
    """In-memory subset of the async Redis client used by registration."""

    def __init__(self, set_error: Exception | None = None) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.deleted: list[str] = []
        self.set_error = set_error

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        if self.set_error is not None:
            raise self.set_error

        self.values[key] = value
        self.ttls[key] = ex

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)
        self.ttls.pop(key, None)

    async def aclose(self) -> None:
        return None


@contextmanager
def registration_client(
    session: FakeSession,
    redis: FakeRedis,
    app_host: str = "localhost:3000",
) -> Iterator[TestClient]:
    """Create an app with fake database and Redis dependencies."""

    settings = Settings(
        proxy_secret=TEST_PROXY_SECRET,
        proxy_signature_ttl_seconds=30,
        log_level="CRITICAL",
        app_host=app_host,
        auto_create_tables=False,
    )
    app = create_app(settings)
    app.state.redis = redis

    async def override_get_session() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        yield client


def find_added(session: FakeSession, model_type: type[Any]) -> Any:
    """Return the single added model instance of the requested type."""

    matches = [model for model in session.added if isinstance(model, model_type)]
    assert len(matches) == 1
    return matches[0]


def test_registration_creates_student_outbox_and_verification_token() -> None:
    session = FakeSession()
    redis = FakeRedis()

    with registration_client(session, redis) as client:
        response = client.post(
            REGISTER_PATH,
            json=VALID_PAYLOAD,
            headers=signed_headers("POST", REGISTER_PATH),
        )

    assert response.status_code == 201
    assert response.json()["user_id"]
    assert response.json()["message"] == (
        "Registration successful. Please verify your email."
    )

    student = find_added(session, Student)
    outbox = find_added(session, Outbox)

    assert response.json()["user_id"] == str(student.user_id)
    assert student.first_name == "Ada"
    assert student.middle_name == "Augusta"
    assert student.last_name == "Lovelace"
    assert student.email == "ada@example.com"
    assert student.is_email_verified is False
    assert student.profile_pic_url is None
    assert student.password_hash != VALID_PAYLOAD["password"]
    assert verify_password(VALID_PAYLOAD["password"], student.password_hash)

    assert outbox.topic == OUTBOX_TOPIC
    assert outbox.status is OutboxStatus.pending
    assert outbox.num_of_retries == 0
    assert outbox.retry_after is None

    assert session.flushed is True
    assert session.committed is True
    assert session.rolled_back is False

    assert len(redis.values) == 1
    token, stored_user_id = next(iter(redis.values.items()))
    assert stored_user_id == str(student.user_id)
    assert redis.ttls[token] == VERIFICATION_TOKEN_TTL_SECONDS
    assert outbox.payload == {
        "recipient_email": "ada@example.com",
        "verification_link": (
            f"http://localhost:3000/email-verification?token={token}"
        ),
    }


def test_registration_uses_https_app_host_when_provided() -> None:
    session = FakeSession()
    redis = FakeRedis()

    with registration_client(
        session,
        redis,
        app_host="https://coursewave.example",
    ) as client:
        response = client.post(
            REGISTER_PATH,
            json=VALID_PAYLOAD,
            headers=signed_headers("POST", REGISTER_PATH),
        )

    assert response.status_code == 201
    token = next(iter(redis.values))
    outbox = find_added(session, Outbox)
    assert outbox.payload["verification_link"] == (
        f"https://coursewave.example/email-verification?token={token}"
    )


def test_duplicate_email_returns_409_and_rolls_back() -> None:
    duplicate_error = IntegrityError(
        "INSERT INTO student",
        {},
        Exception("duplicate key value violates unique constraint"),
    )
    session = FakeSession(flush_error=duplicate_error)
    redis = FakeRedis()

    with registration_client(session, redis) as client:
        response = client.post(
            REGISTER_PATH,
            json=VALID_PAYLOAD,
            headers=signed_headers("POST", REGISTER_PATH),
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "Email is already registered"}
    assert session.rolled_back is True
    assert session.committed is False
    assert redis.values == {}
    assert redis.deleted == []
    assert find_added(session, Student).email == "ada@example.com"


def test_redis_failure_returns_503_and_rolls_back() -> None:
    session = FakeSession()
    redis = FakeRedis(set_error=RedisError("redis unavailable"))

    with registration_client(session, redis) as client:
        response = client.post(
            REGISTER_PATH,
            json=VALID_PAYLOAD,
            headers=signed_headers("POST", REGISTER_PATH),
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Email verification service unavailable"
    }
    assert session.rolled_back is True
    assert session.committed is False
    assert session.flushed is True
    assert redis.values == {}


def test_registration_requires_gateway_signature() -> None:
    session = FakeSession()
    redis = FakeRedis()

    with registration_client(session, redis) as client:
        response = client.post(REGISTER_PATH, json=VALID_PAYLOAD)

    assert response.status_code == 403
    assert response.json() == {"detail": "Missing proxy signature headers"}
    assert session.added == []
    assert redis.values == {}


def test_invalid_registration_payload_returns_422_without_writes() -> None:
    session = FakeSession()
    redis = FakeRedis()
    payload = {**VALID_PAYLOAD, "password": "too-weak", "confirm_password": "too-weak"}

    with registration_client(session, redis) as client:
        response = client.post(
            REGISTER_PATH,
            json=payload,
            headers=signed_headers("POST", REGISTER_PATH),
        )

    assert response.status_code == 422
    assert session.added == []
    assert redis.values == {}


def test_build_verification_link_adds_http_scheme_when_missing() -> None:
    assert build_verification_link("localhost:3000/", "abc") == (
        "http://localhost:3000/email-verification?token=abc"
    )
