"""Tests for the student registration endpoint."""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError

from app.base_dependencies import get_session
from app.main import create_app
from app.security import verify_password
from app.services.student.model import AuthMethod, Student
from app.services.student.router.student import VERIFICATION_TOKEN_TTL_SECONDS
from tests.fakes import FakeRedis
from tests.helpers import build_settings

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


@contextmanager
def registration_client(
    session: FakeSession,
    redis: FakeRedis,
) -> Iterator[TestClient]:
    """Create an app with fake database and Redis dependencies."""

    settings = build_settings(
        auto_create_tables=False,
        cache_enabled=False,
        rate_limit_enabled=False,
    )
    app = create_app(settings, redis_client=redis)  # type: ignore[arg-type]

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


def test_registration_creates_student_and_verification_token() -> None:
    session = FakeSession()
    redis = FakeRedis()

    with registration_client(session, redis) as client:
        response = client.post(
            REGISTER_PATH,
            json=VALID_PAYLOAD,
        )

    assert response.status_code == 201
    assert response.json()["user_id"]
    assert response.json()["message"] == (
        "Registration successful. Please verify your email."
    )

    student = find_added(session, Student)

    assert len(session.added) == 1
    assert response.json()["user_id"] == str(student.user_id)
    assert student.first_name == "Ada"
    assert student.middle_name == "Augusta"
    assert student.last_name == "Lovelace"
    assert student.email == "ada@example.com"
    assert student.is_email_verified is False
    assert student.profile_pic_url is None
    assert student.auth_method is AuthMethod.EMAIL
    assert student.password_hash is not None
    assert student.password_hash != VALID_PAYLOAD["password"]
    assert verify_password(VALID_PAYLOAD["password"], student.password_hash)

    assert session.flushed is True
    assert session.committed is True
    assert session.rolled_back is False

    assert len(redis.values) == 1
    token, stored_user_id = next(iter(redis.values.items()))
    assert stored_user_id == str(student.user_id)
    assert redis.ttls[token] == VERIFICATION_TOKEN_TTL_SECONDS


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
    redis = FakeRedis()
    redis.fail("set", RedisError("redis unavailable"))

    with registration_client(session, redis) as client:
        response = client.post(
            REGISTER_PATH,
            json=VALID_PAYLOAD,
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Email verification service unavailable"
    }
    assert session.rolled_back is True
    assert session.committed is False
    assert session.flushed is True
    assert redis.values == {}


def test_invalid_registration_payload_returns_422_without_writes() -> None:
    session = FakeSession()
    redis = FakeRedis()
    payload = {**VALID_PAYLOAD, "password": "too-weak", "confirm_password": "too-weak"}

    with registration_client(session, redis) as client:
        response = client.post(
            REGISTER_PATH,
            json=payload,
        )

    assert response.status_code == 422
    assert session.added == []
    assert redis.values == {}


def test_student_model_defaults_password_hash_and_auth_method() -> None:
    student = Student(
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.com",
    )

    assert student.password_hash is None
    assert student.auth_method is AuthMethod.EMAIL
    assert Student.__table__.c.password_hash.nullable is True
    assert Student.__table__.c.auth_method.nullable is False
