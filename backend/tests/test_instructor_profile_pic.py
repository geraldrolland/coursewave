"""Tests for instructor profile-picture upload and deletion."""

from pathlib import PurePath
from uuid import UUID, uuid4

import pytest

from app.middleware.auth import MISSING_AUTHORIZATION
from app.services.instructor.model import Instructor
from app.services.instructor.router.instructor import (
    PROFILE_PICTURE_DELETED,
    PROFILE_PICTURE_NOT_SET,
    PROFILE_PICTURE_UNAVAILABLE,
)
from app.services.instructor.schema import (
    INSTRUCTOR_PROFILE_PICTURE_MAX_BYTES,
)
from app.services.instructor.utils import (
    INSTRUCTOR_NOT_FOUND,
    INVALID_AUTH_SESSION,
)
from tests.fakes import FakeRedis
from tests.helpers import access_token, auth_headers, seed_session
from tests.instructor_fakes import (
    DEFAULT_EMAIL,
    FakeInstructorSession,
    FakeMinio,
    instructor_client,
    make_instructor,
)

PROFILE_PIC_PATH = "/instructor/api/v1/profile-pic"
BUCKET = "profilepics"
PUBLIC_URL = "http://localhost:9000"
IMAGE_BYTES = b"instructor-profile-picture"


def profile_headers() -> dict[str, str]:
    """Return authentication headers for the instructor session."""

    return auth_headers(access_token())


def seed_instructor_session(
    redis: FakeRedis,
    instructor: Instructor,
) -> None:
    """Store an instructor session document."""

    seed_session(
        redis,
        user={
            "id": str(instructor.instructor_id),
            "email": instructor.email,
            "role": "instructor",
        },
    )


def profile_pic_files(
    filename: str = "photo.jpg",
    data: bytes = IMAGE_BYTES,
) -> dict[str, tuple[str, bytes, str]]:
    """Build the multipart profile picture upload."""

    return {"profile_pic": (filename, data, "image/jpeg")}


def object_key_from_url(url: str) -> str:
    """Return the object key from a profile picture URL."""

    return url.rsplit("/", 1)[-1]


def test_upload_profile_pic_creates_uuid_object_and_url() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
            files=profile_pic_files(),
        )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"profile_pic_url"}
    object_key = object_key_from_url(payload["profile_pic_url"])
    assert PurePath(object_key).suffix == ".jpg"
    UUID(object_key.rsplit(".", 1)[0])
    assert payload["profile_pic_url"] == f"{PUBLIC_URL}/{BUCKET}/{object_key}"
    assert instructor.profile_pic_url == payload["profile_pic_url"]

    assert len(minio.uploads) == 1
    upload = minio.uploads[0]
    assert upload["bucket"] == BUCKET
    assert upload["object_key"] == object_key
    assert upload["data"] == IMAGE_BYTES
    assert upload["length"] == len(IMAGE_BYTES)
    assert upload["content_type"] == "image/jpeg"
    assert session.commits == 1


def test_upload_profile_pic_overwrites_existing_object() -> None:
    instructor = make_instructor(password=None)
    instructor.profile_pic_url = f"{PUBLIC_URL}/{BUCKET}/existing.jpeg"
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
            files=profile_pic_files("new.jpeg"),
        )

    assert response.status_code == 200
    assert response.json() == {
        "profile_pic_url": f"{PUBLIC_URL}/{BUCKET}/existing.jpeg"
    }
    assert len(minio.uploads) == 1
    assert minio.uploads[0]["object_key"] == "existing.jpeg"
    assert instructor.profile_pic_url == (
        f"{PUBLIC_URL}/{BUCKET}/existing.jpeg"
    )
    assert session.commits == 1


def test_upload_profile_pic_replaces_unparseable_url() -> None:
    instructor = make_instructor(password=None)
    instructor.profile_pic_url = "https://example.com/avatar.jpg"
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
            files=profile_pic_files(),
        )

    assert response.status_code == 200
    url = response.json()["profile_pic_url"]
    assert url.startswith(f"{PUBLIC_URL}/{BUCKET}/")
    assert len(minio.uploads) == 1
    assert session.commits == 1


def test_upload_profile_pic_rejects_extra_form_fields() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
            data={"extra": "field"},
            files=profile_pic_files(),
        )

    assert response.status_code == 422
    assert minio.uploads == []
    assert session.commits == 0


def test_upload_profile_pic_rejects_multiple_files() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
            files=[
                ("profile_pic", ("one.jpg", IMAGE_BYTES, "image/jpeg")),
                ("profile_pic", ("two.jpg", IMAGE_BYTES, "image/jpeg")),
            ],
        )

    assert response.status_code == 422
    assert minio.uploads == []
    assert session.commits == 0


def test_upload_profile_pic_requires_a_file() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
        )

    assert response.status_code == 422
    assert minio.uploads == []


def test_upload_profile_pic_requires_authentication() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PIC_PATH,
            files=profile_pic_files(),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_AUTHORIZATION}
    assert minio.uploads == []


def test_upload_profile_pic_rejects_incomplete_session() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_session(
        redis,
        user={"email": instructor.email, "role": "instructor"},
    )

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
            files=profile_pic_files(),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_AUTH_SESSION}
    assert minio.uploads == []


def test_upload_profile_pic_returns_404_for_unknown_instructor() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()
    minio = FakeMinio()
    seed_session(
        redis,
        user={
            "id": str(uuid4()),
            "email": DEFAULT_EMAIL,
            "role": "instructor",
        },
    )

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
            files=profile_pic_files(),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": INSTRUCTOR_NOT_FOUND}
    assert minio.uploads == []


@pytest.mark.parametrize(
    "filename",
    ["photo.png", "photo.gif", "photo.txt", "photo"],
)
def test_upload_profile_pic_rejects_invalid_extension(
    filename: str,
) -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
            files=profile_pic_files(filename),
        )

    assert response.status_code == 422
    assert minio.uploads == []


def test_upload_profile_pic_rejects_empty_file() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
            files=profile_pic_files(data=b""),
        )

    assert response.status_code == 422
    assert minio.uploads == []


def test_upload_profile_pic_rejects_file_at_five_megabytes() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
            files=profile_pic_files(
                data=b"x" * INSTRUCTOR_PROFILE_PICTURE_MAX_BYTES
            ),
        )

    assert response.status_code == 422
    assert minio.uploads == []


def test_upload_profile_pic_minio_failure_returns_503() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    minio.upload_error = RuntimeError("minio unavailable")
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
            files=profile_pic_files(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": PROFILE_PICTURE_UNAVAILABLE}
    assert session.rollbacks == 1
    assert session.commits == 0


def test_upload_profile_pic_commit_failure_cleans_up_object() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
            files=profile_pic_files(),
        )

    assert response.status_code == 500
    assert session.rollbacks == 1
    assert len(minio.uploads) == 1
    object_key = minio.uploads[0]["object_key"]
    assert (BUCKET, object_key) in minio.deleted


def test_delete_profile_pic_removes_object_and_clears_url() -> None:
    instructor = make_instructor(password=None)
    instructor.profile_pic_url = f"{PUBLIC_URL}/{BUCKET}/existing.jpg"
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.delete(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {"message": PROFILE_PICTURE_DELETED}
    assert minio.deleted == [(BUCKET, "existing.jpg")]
    assert instructor.profile_pic_url is None
    assert session.commits == 1


def test_delete_profile_pic_is_idempotent_when_not_set() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.delete(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {"message": PROFILE_PICTURE_NOT_SET}
    assert minio.deleted == []
    assert session.commits == 0


def test_delete_profile_pic_clears_unparseable_url() -> None:
    instructor = make_instructor(password=None)
    instructor.profile_pic_url = "https://example.com/avatar.jpg"
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.delete(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {"message": PROFILE_PICTURE_DELETED}
    assert minio.deleted == []
    assert instructor.profile_pic_url is None
    assert session.commits == 1


def test_delete_profile_pic_minio_failure_returns_503() -> None:
    instructor = make_instructor(password=None)
    instructor.profile_pic_url = f"{PUBLIC_URL}/{BUCKET}/existing.jpg"
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    minio.delete_error = RuntimeError("minio unavailable")
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.delete(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": PROFILE_PICTURE_UNAVAILABLE}
    assert instructor.profile_pic_url == (
        f"{PUBLIC_URL}/{BUCKET}/existing.jpg"
    )
    assert session.commits == 0


def test_delete_profile_pic_commit_failure_rolls_back() -> None:
    instructor = make_instructor(password=None)
    instructor.profile_pic_url = f"{PUBLIC_URL}/{BUCKET}/existing.jpg"
    session = FakeInstructorSession(instructor)
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.delete(
            PROFILE_PIC_PATH,
            headers=profile_headers(),
        )

    assert response.status_code == 500
    assert session.rollbacks == 1
