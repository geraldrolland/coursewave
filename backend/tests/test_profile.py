"""Tests for the student profile endpoints."""

from pathlib import PurePath
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.middleware.auth import MISSING_AUTHORIZATION
from app.services.student.model import Student
from app.services.student.router import (
    INVALID_AUTH_SESSION,
    PROFILE_PICTURE_UNAVAILABLE,
    STUDENT_NOT_FOUND,
)
from app.services.student.schema import PROFILE_PICTURE_MAX_BYTES
from tests.fakes import FakeRedis
from tests.helpers import (
    access_token,
    auth_headers,
    seed_session,
)
from tests.student_fakes import (
    FakeMinio,
    FakeStudentSession,
    make_student,
    student_client,
)

PROFILE_PATH = "/student/api/v1/profile"
BUCKET = "profilepics"
PUBLIC_URL = "http://localhost:9000"
IMAGE_BYTES = b"profile-picture-bytes"


def seed_student_session(redis: FakeRedis, student: Student) -> None:
    """Store a session document whose user ID belongs to the student."""

    seed_session(
        redis,
        user={
            "id": str(student.user_id),
            "email": student.email,
            "role": "student",
        },
    )


def profile_headers() -> dict[str, str]:
    """Return authentication headers for the test session."""

    return auth_headers(access_token())


def object_key_from_response(response_json: dict[str, object]) -> str:
    """Return the object key from a profile response URL."""

    url = response_json["profile_pic_url"]
    assert isinstance(url, str)
    return url.rsplit("/", 1)[-1]


def test_get_profile_returns_authenticated_student() -> None:
    student = make_student()
    student.middle_name = "Augusta"
    session = FakeStudentSession(student)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_student_session(redis, student)

    with student_client(session, redis, minio_client=minio) as client:
        response = client.get(PROFILE_PATH, headers=profile_headers())

    assert response.status_code == 200
    assert response.json() == {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": student.email,
        "profile_pic_url": None,
    }
    assert session.commits == 0
    assert minio.uploads == []
    assert minio.deleted == []


def test_get_profile_returns_existing_picture_url() -> None:
    student = make_student()
    student.profile_pic_url = f"{PUBLIC_URL}/{BUCKET}/avatar.png"
    session = FakeStudentSession(student)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_student_session(redis, student)

    with student_client(session, redis, minio_client=minio) as client:
        response = client.get(PROFILE_PATH, headers=profile_headers())

    assert response.status_code == 200
    assert response.json() == {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": student.email,
        "profile_pic_url": student.profile_pic_url,
    }
    assert session.commits == 0


def test_get_profile_requires_authentication() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()

    with student_client(session, redis) as client:
        response = client.get(PROFILE_PATH)

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_AUTHORIZATION}
    assert session.commits == 0


def test_get_profile_rejects_non_uuid_session_user() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()
    seed_session(redis)

    with student_client(session, redis) as client:
        response = client.get(PROFILE_PATH, headers=profile_headers())

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_AUTH_SESSION}
    assert session.commits == 0


def test_get_profile_returns_404_for_unknown_student() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()
    seed_student_session(redis, student)
    session.student = None

    with student_client(session, redis) as client:
        response = client.get(PROFILE_PATH, headers=profile_headers())

    assert response.status_code == 404
    assert response.json() == {"detail": STUDENT_NOT_FOUND}
    assert session.commits == 0


def test_name_update_succeeds_without_touching_storage() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_student_session(redis, student)

    with student_client(session, redis, minio_client=minio) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            data={"first_name": "Grace", "middlename": "Brewster"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(student.user_id),
        "first_name": "Grace",
        "middle_name": "Brewster",
        "last_name": "Lovelace",
        "email": student.email,
        "profile_pic_url": None,
    }
    assert student.first_name == "Grace"
    assert student.middle_name == "Brewster"
    assert session.commits == 1
    assert minio.uploads == []
    assert minio.deleted == []


def test_name_only_update_preserves_existing_picture() -> None:
    student = make_student()
    student.profile_pic_url = f"{PUBLIC_URL}/{BUCKET}/existing.png"
    session = FakeStudentSession(student)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_student_session(redis, student)

    with student_client(session, redis, minio_client=minio) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            data={"first_name": "Grace"},
        )

    assert response.status_code == 200
    assert response.json()["profile_pic_url"] == student.profile_pic_url
    assert minio.uploads == []
    assert minio.deleted == []


@pytest.mark.parametrize(
    ("filename", "expected_extension", "expected_content_type"),
    [
        ("photo.jpg", ".jpg", "image/jpeg"),
        ("photo.jpeg", ".jpeg", "image/jpeg"),
        ("photo.png", ".png", "image/png"),
        ("PHOTO.JPG", ".jpg", "image/jpeg"),
        ("photo.PnG", ".png", "image/png"),
    ],
)
def test_profile_picture_upload_stores_uuid_object(
    filename: str,
    expected_extension: str,
    expected_content_type: str,
) -> None:
    student = make_student()
    student.profile_pic_url = None
    session = FakeStudentSession(student)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_student_session(redis, student)

    with student_client(session, redis, minio_client=minio) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            files={
                "profile_pic": (filename, IMAGE_BYTES, "image/jpeg"),
            },
        )

    assert response.status_code == 200
    payload = response.json()
    object_key = object_key_from_response(payload)
    assert PurePath(object_key).suffix == expected_extension
    UUID(object_key.rsplit(".", 1)[0])

    assert len(minio.uploads) == 1
    upload = minio.uploads[0]
    assert upload["bucket"] == BUCKET
    assert upload["object_key"] == object_key
    assert upload["data"] == IMAGE_BYTES
    assert upload["length"] == len(IMAGE_BYTES)
    assert upload["content_type"] == expected_content_type
    assert payload["profile_pic_url"] == (
        f"{PUBLIC_URL}/{BUCKET}/{object_key}"
    )
    assert student.profile_pic_url == payload["profile_pic_url"]
    assert session.commits == 1


def test_profile_picture_replacement_overwrites_previous_object() -> None:
    student = make_student()
    old_key = "11111111-1111-4111-8111-111111111111.png"
    old_url = f"{PUBLIC_URL}/{BUCKET}/{old_key}"
    student.profile_pic_url = old_url
    session = FakeStudentSession(student)
    redis = FakeRedis()
    minio = FakeMinio()
    minio.objects[(BUCKET, old_key)] = {"data": b"old"}
    seed_student_session(redis, student)

    with student_client(session, redis, minio_client=minio) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            files={"profile_pic": ("new.jpg", IMAGE_BYTES, "image/jpeg")},
        )

    assert response.status_code == 200
    assert response.json()["profile_pic_url"] == old_url
    assert student.profile_pic_url == old_url
    assert len(minio.uploads) == 1
    upload = minio.uploads[0]
    assert upload["object_key"] == old_key
    assert upload["data"] == IMAGE_BYTES
    assert upload["content_type"] == "image/jpeg"
    assert minio.deleted == []
    assert minio.objects[(BUCKET, old_key)]["data"] == IMAGE_BYTES
    assert session.commits == 1


def test_unparseable_existing_url_is_ignored() -> None:
    student = make_student()
    old_url = "https://cdn.example.com/avatar.png"
    student.profile_pic_url = old_url
    session = FakeStudentSession(student)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_student_session(redis, student)

    with student_client(session, redis, minio_client=minio) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            files={"profile_pic": ("new.jpg", IMAGE_BYTES, "image/jpeg")},
        )

    assert response.status_code == 200
    assert minio.deleted == []
    assert response.json()["profile_pic_url"] != old_url


@pytest.mark.parametrize(
    "filename",
    ["photo.gif", "photo.pdf", "photo", "photo.jpg.exe", ""],
)
def test_unsupported_profile_picture_extension_returns_422(
    filename: str,
) -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_student_session(redis, student)

    with student_client(session, redis, minio_client=minio) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            files={"profile_pic": (filename, IMAGE_BYTES, "image/jpeg")},
        )

    assert response.status_code == 422
    assert minio.uploads == []
    assert session.commits == 0
    assert student.profile_pic_url is None


def test_profile_picture_at_size_limit_returns_422() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_student_session(redis, student)

    with student_client(session, redis, minio_client=minio) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            files={
                "profile_pic": (
                    "photo.jpg",
                    b"x" * PROFILE_PICTURE_MAX_BYTES,
                    "image/jpeg",
                ),
            },
        )

    assert response.status_code == 422
    assert minio.uploads == []
    assert session.commits == 0


def test_empty_profile_picture_returns_422() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_student_session(redis, student)

    with student_client(session, redis, minio_client=minio) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            files={"profile_pic": ("photo.jpg", b"", "image/jpeg")},
        )

    assert response.status_code == 422
    assert minio.uploads == []
    assert session.commits == 0


def test_empty_profile_update_returns_422() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_student_session(redis, student)

    with student_client(session, redis, minio_client=minio) as client:
        response = client.patch(PROFILE_PATH, headers=profile_headers())

    assert response.status_code == 422
    assert minio.uploads == []
    assert session.commits == 0


def test_profile_update_requires_authentication() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()
    minio = FakeMinio()

    with student_client(session, redis, minio_client=minio) as client:
        response = client.patch(
            PROFILE_PATH,
            data={"first_name": "Grace"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_AUTHORIZATION}
    assert session.commits == 0


def test_profile_update_rejects_non_uuid_session_user() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_session(redis)

    with student_client(session, redis, minio_client=minio) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            data={"first_name": "Grace"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_AUTH_SESSION}
    assert session.commits == 0


def test_profile_update_returns_404_for_unknown_student() -> None:
    student = make_student()
    session = FakeStudentSession(student)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_student_session(redis, student)
    session.student = None

    with student_client(session, redis, minio_client=minio) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            data={"first_name": "Grace"},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": STUDENT_NOT_FOUND}
    assert session.commits == 0


def test_upload_failure_returns_503_without_database_change() -> None:
    student = make_student()
    student.profile_pic_url = f"{PUBLIC_URL}/{BUCKET}/existing.png"
    original_url = student.profile_pic_url
    session = FakeStudentSession(student)
    redis = FakeRedis()
    minio = FakeMinio()
    minio.upload_error = RuntimeError("minio unavailable")
    seed_student_session(redis, student)

    with student_client(session, redis, minio_client=minio) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            data={"first_name": "Grace"},
            files={"profile_pic": ("new.jpg", IMAGE_BYTES, "image/jpeg")},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": PROFILE_PICTURE_UNAVAILABLE}
    assert session.commits == 0
    assert session.rollbacks == 0
    assert student.first_name == "Ada"
    assert student.profile_pic_url == original_url
    assert minio.deleted == []


def test_commit_failure_removes_the_new_object() -> None:
    student = make_student()
    student.profile_pic_url = None
    session = FakeStudentSession(student)
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()
    minio = FakeMinio()
    seed_student_session(redis, student)

    with student_client(session, redis, minio_client=minio) as client:
        with pytest.raises(RuntimeError, match="database unavailable"):
            client.patch(
                PROFILE_PATH,
                headers=profile_headers(),
                files={
                    "profile_pic": ("new.jpg", IMAGE_BYTES, "image/jpeg"),
                },
            )

    assert session.commits == 0
    assert session.rollbacks == 1
    assert len(minio.uploads) == 1
    new_object_key = minio.uploads[0]["object_key"]
    assert minio.deleted == [(BUCKET, new_object_key)]
    assert minio.objects == {}


def test_commit_failure_keeps_overwritten_object() -> None:
    student = make_student()
    old_key = "33333333-3333-4333-8333-333333333333.png"
    old_url = f"{PUBLIC_URL}/{BUCKET}/{old_key}"
    student.profile_pic_url = old_url
    session = FakeStudentSession(student)
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()
    minio = FakeMinio()
    minio.objects[(BUCKET, old_key)] = {"data": b"old"}
    seed_student_session(redis, student)

    with student_client(session, redis, minio_client=minio) as client:
        with pytest.raises(RuntimeError, match="database unavailable"):
            client.patch(
                PROFILE_PATH,
                headers=profile_headers(),
                files={
                    "profile_pic": ("new.jpg", IMAGE_BYTES, "image/jpeg"),
                },
            )

    assert session.commits == 0
    assert session.rollbacks == 1
    assert len(minio.uploads) == 1
    assert minio.uploads[0]["object_key"] == old_key
    assert minio.deleted == []
    assert minio.objects[(BUCKET, old_key)]["data"] == IMAGE_BYTES
    assert student.profile_pic_url == old_url
