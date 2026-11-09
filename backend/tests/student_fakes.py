"""Shared fakes and app helpers for student endpoint tests."""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient

from app.base_dependencies import get_session
from app.main import create_app
from app.security import hash_password
from app.services.student.model import Student
from tests.helpers import build_settings

DEFAULT_EMAIL = "ada@example.com"
DEFAULT_PASSWORD = "StrongPass1!"


class FakeScalarResult:
    """Minimal scalar result double for ``AsyncSession.execute``."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class FakeStudentSession:
    """Minimal async session double for student account routes."""

    def __init__(self, student: Student | None = None) -> None:
        self.student = student
        self.added: list[Any] = []
        self.executed: list[Any] = []
        self.get_calls: list[tuple[Any, Any]] = []
        self.commits = 0
        self.rollbacks = 0
        self.flushed = False
        self.commit_error: Exception | None = None
        self.flush_error: Exception | None = None

    async def execute(self, statement: Any) -> FakeScalarResult:
        self.executed.append(statement)
        return FakeScalarResult(self.student)

    async def get(
        self,
        model: type[Any],
        identifier: Any,
        **_: Any,
    ) -> Any:
        self.get_calls.append((model, identifier))
        if (
            model is Student
            and self.student is not None
            and self.student.user_id == identifier
        ):
            return self.student
        return None

    def add(self, model: Any) -> None:
        self.added.append(model)

    async def flush(self) -> None:
        self.flushed = True
        if self.flush_error is not None:
            raise self.flush_error

    async def commit(self) -> None:
        if self.commit_error is not None:
            raise self.commit_error
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def make_student(
    *,
    email: str = DEFAULT_EMAIL,
    is_email_verified: bool = True,
    password: str | None = DEFAULT_PASSWORD,
    password_hash: str | None = None,
) -> Student:
    """Build a student with a real password hash by default."""

    if password_hash is None and password is not None:
        password_hash = hash_password(password)

    return Student(
        first_name="Ada",
        last_name="Lovelace",
        email=email,
        password_hash=password_hash,
        is_email_verified=is_email_verified,
    )


@contextmanager
def student_client(
    session: FakeStudentSession,
    redis: Any,
    **settings_overrides: Any,
) -> Iterator[TestClient]:
    """Create an app with fake database and Redis dependencies."""

    settings = build_settings(
        auto_create_tables=False,
        cache_enabled=False,
        rate_limit_enabled=False,
        **settings_overrides,
    )
    app = create_app(settings, redis_client=redis)  # type: ignore[arg-type]

    async def override_get_session() -> AsyncIterator[FakeStudentSession]:
        yield session

    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        yield client
