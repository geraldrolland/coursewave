"""Shared fakes and app helpers for instructor endpoint tests."""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient

from app.base_dependencies import get_session
from app.main import create_app
from app.security import hash
from app.services.instructor.model import Instructor
from tests.helpers import build_settings

DEFAULT_EMAIL = "ada@example.com"
DEFAULT_PASSWORD = "StrongPass1!"


class FakeScalarResult:
    """Minimal scalar result double for ``AsyncSession.execute``."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class FakeInstructorSession:
    """Minimal async session double for instructor account routes."""

    def __init__(self, instructor: Instructor | None = None) -> None:
        self.instructor = instructor
        self.added: list[Any] = []
        self.executed: list[Any] = []
        self.get_calls: list[tuple[Any, Any]] = []
        self.commits = 0
        self.rollbacks = 0
        self.commit_error: Exception | None = None

    async def execute(self, statement: Any) -> FakeScalarResult:
        self.executed.append(statement)
        return FakeScalarResult(self.instructor)

    async def get(
        self,
        model: type[Any],
        identifier: Any,
        **_: Any,
    ) -> Any:
        self.get_calls.append((model, identifier))
        if (
            model is Instructor
            and self.instructor is not None
            and self.instructor.instructor_id == identifier
        ):
            return self.instructor
        return None

    def add(self, model: Any) -> None:
        self.added.append(model)

    async def commit(self) -> None:
        if self.commit_error is not None:
            raise self.commit_error
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeMinio:
    """In-memory MinIO client double for instructor profile-picture tests."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.uploads: list[dict[str, Any]] = []
        self.deleted: list[tuple[str, str]] = []
        self.upload_error: Exception | None = None
        self.delete_error: Exception | None = None

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: Any,
        length: int,
        content_type: str = "application/octet-stream",
        **_: Any,
    ) -> None:
        if self.upload_error is not None:
            raise self.upload_error

        body = data.read()
        record = {
            "bucket": bucket_name,
            "object_key": object_name,
            "data": body,
            "length": length,
            "content_type": content_type,
        }
        self.objects[(bucket_name, object_name)] = record
        self.uploads.append(record)

    def remove_object(
        self,
        bucket_name: str,
        object_name: str,
        version_id: str | None = None,
    ) -> None:
        if self.delete_error is not None:
            raise self.delete_error

        self.deleted.append((bucket_name, object_name))
        self.objects.pop((bucket_name, object_name), None)


def make_instructor(
    *,
    email: str = DEFAULT_EMAIL,
    password: str | None = DEFAULT_PASSWORD,
    password_hash: str | None = None,
    is_email_verified: bool = True,
    is_2fa_enabled: bool = False,
) -> Instructor:
    """Build an instructor with a real password hash by default."""

    if password_hash is None and password is not None:
        password_hash = hash(password)

    return Instructor(
        email=email,
        password_hash=password_hash,
        is_email_verified=is_email_verified,
        is_2fa_enabled=is_2fa_enabled,
    )


@contextmanager
def instructor_client(
    session: FakeInstructorSession,
    redis: Any,
    minio_client: Any = None,
    app_host: str = "localhost:3000",
    **settings_overrides: Any,
) -> Iterator[TestClient]:
    """Create an app with fake database, Redis, and MinIO dependencies."""

    settings = build_settings(
        app_host=app_host,
        auto_create_tables=False,
        cache_enabled=False,
        rate_limit_enabled=False,
        **settings_overrides,
    )
    app = create_app(
        settings,
        redis_client=redis,  # type: ignore[arg-type]
        minio_client=minio_client,
    )

    async def override_get_session() -> AsyncIterator[FakeInstructorSession]:
        yield session

    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
