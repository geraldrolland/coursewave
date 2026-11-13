"""Tests for instructor profile creation."""

import json
from pathlib import PurePath
from uuid import UUID, uuid4

import pytest

from app.middleware.auth import MISSING_AUTHORIZATION
from app.services.instructor.model import Gender, Instructor, InstructorProfile
from app.services.instructor.router.profile import (
    PROFILE_ALREADY_EXISTS,
    PROFILE_PICTURE_UNAVAILABLE,
)
from app.services.instructor.schema import PROFILE_PICTURE_MAX_BYTES
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

PROFILE_PATH = "/instructor/api/v1/profile"
BUCKET = "profilepics"
PUBLIC_URL = "http://localhost:9000"
IMAGE_BYTES = b"instructor-profile-picture"
LINKEDIN_URL = "https://www.linkedin.com/in/ada"
X_URL = "https://x.com/ada"
SOCIAL_LINKS = [{"linkedin": LINKEDIN_URL, "x": X_URL}]


def profile_data(**overrides: object) -> dict[str, object]:
    """Build the default multipart form fields."""

    data: dict[str, object] = {
        "first_name": "Ada",
        "middle_name": "Augusta",
        "last_name": "Lovelace",
        "gender": "female",
        "country_of_residence": "United Kingdom",
        "state": "England",
        "city": "London",
        "bio": "Mathematician and writer.",
        "headline": "Software engineer",
        "current_occupation": "Engineer",
        "years_of_exp": 10,
        "expertise": json.dumps(["tech", "science"]),
        "social_links": json.dumps(SOCIAL_LINKS),
        "company": "CourseWave",
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


def profile_files(filename: str = "photo.png") -> dict[str, tuple[str, bytes, str]]:
    """Build the multipart profile picture file."""

    return {"profile_pic": (filename, IMAGE_BYTES, "image/png")}


def profile_headers() -> dict[str, str]:
    """Return authentication headers for the test session."""

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


def make_existing_profile(instructor: Instructor) -> InstructorProfile:
    """Build an existing profile for the conflict test."""

    return InstructorProfile(
        instructor_id=instructor.instructor_id,
        first_name="Ada",
        last_name="Lovelace",
        email=instructor.email,
        gender=Gender.FEMALE,
        country_of_residence="United Kingdom",
        state="England",
        city="London",
        bio="Existing profile.",
        headline="Engineer",
        profile_pic_url=f"{PUBLIC_URL}/{BUCKET}/existing.png",
        expertise=["tech"],
        years_of_exp=10,
        current_occupation="Engineer",
    )


def find_profile(session: FakeInstructorSession) -> InstructorProfile:
    """Return the created profile from the fake session."""

    profiles = [
        model for model in session.added if isinstance(model, InstructorProfile)
    ]
    assert len(profiles) == 1
    return profiles[0]


def test_create_profile_uploads_picture_and_returns_profile() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PATH,
            headers=profile_headers(),
            data=profile_data(),
            files=profile_files(),
        )

    assert response.status_code == 201
    payload = response.json()
    profile = find_profile(session)

    assert payload["profile_id"] == str(profile.profile_id)
    assert payload["instructor_id"] == str(instructor.instructor_id)
    assert payload["email"] == DEFAULT_EMAIL
    assert payload["first_name"] == "Ada"
    assert payload["middle_name"] == "Augusta"
    assert payload["last_name"] == "Lovelace"
    assert payload["gender"] == "female"
    assert payload["country_of_residence"] == "United Kingdom"
    assert payload["state"] == "England"
    assert payload["city"] == "London"
    assert payload["bio"] == "Mathematician and writer."
    assert payload["headline"] == "Software engineer"
    assert payload["current_occupation"] == "Engineer"
    assert payload["years_of_exp"] == 10
    assert payload["expertise"] == ["tech", "science"]
    assert payload["social_links"] == SOCIAL_LINKS
    assert payload["company"] == "CourseWave"

    object_key = payload["profile_pic_url"].rsplit("/", 1)[-1]
    assert PurePath(object_key).suffix == ".png"
    UUID(object_key.rsplit(".", 1)[0])
    assert payload["profile_pic_url"] == f"{PUBLIC_URL}/{BUCKET}/{object_key}"

    assert len(minio.uploads) == 1
    upload = minio.uploads[0]
    assert upload["bucket"] == BUCKET
    assert upload["object_key"] == object_key
    assert upload["data"] == IMAGE_BYTES
    assert upload["length"] == len(IMAGE_BYTES)
    assert upload["content_type"] == "image/png"

    assert profile.social_links == SOCIAL_LINKS
    assert session.commits == 1


def test_create_profile_accepts_optional_fields_and_repeated_form_values() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    data = profile_data(middle_name=None, company=None, social_links=None)
    data["expertise"] = ["tech", "science"]
    data["social_links"] = [
        json.dumps({"linkedin": LINKEDIN_URL}),
        json.dumps({"x": X_URL}),
    ]

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PATH,
            headers=profile_headers(),
            data=data,
            files=profile_files("photo.jpeg"),
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["middle_name"] is None
    assert payload["company"] is None
    assert payload["expertise"] == ["tech", "science"]
    assert payload["social_links"] == [
        {"linkedin": LINKEDIN_URL},
        {"x": X_URL},
    ]
    assert PurePath(payload["profile_pic_url"]).suffix == ".jpeg"


def test_create_profile_rejects_existing_profile() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(
        instructor,
        profile=make_existing_profile(instructor),
    )
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PATH,
            headers=profile_headers(),
            data=profile_data(),
            files=profile_files(),
        )

    assert response.status_code == 403
    assert response.json() == {"detail": PROFILE_ALREADY_EXISTS}
    assert minio.uploads == []
    assert session.added == []
    assert session.commits == 0


def test_create_profile_requires_authentication() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PATH,
            data=profile_data(),
            files=profile_files(),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_AUTHORIZATION}
    assert minio.uploads == []


def test_create_profile_rejects_incomplete_session() -> None:
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
            PROFILE_PATH,
            headers=profile_headers(),
            data=profile_data(),
            files=profile_files(),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_AUTH_SESSION}
    assert minio.uploads == []


def test_create_profile_returns_404_for_unknown_instructor() -> None:
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
            PROFILE_PATH,
            headers=profile_headers(),
            data=profile_data(),
            files=profile_files(),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": INSTRUCTOR_NOT_FOUND}
    assert minio.uploads == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"first_name": None},
        {"last_name": None},
        {"gender": "unknown"},
        {"country_of_residence": None},
        {"state": None},
        {"city": None},
        {"bio": None},
        {"bio": "x" * 1001},
        {"headline": None},
        {"current_occupation": None},
        {"years_of_exp": -1},
        {"expertise": "[]"},
        {"expertise": "[not-json]"},
        {"social_links": "[]"},
        {"social_links": "[{}]"},
        {"social_links": "{not-json}"},
    ],
)
def test_create_profile_rejects_invalid_form_fields(
    overrides: dict[str, object],
) -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PATH,
            headers=profile_headers(),
            data=profile_data(**overrides),
            files=profile_files(),
        )

    assert response.status_code == 422
    assert minio.uploads == []
    assert session.added == []
    assert session.commits == 0


@pytest.mark.parametrize(
    "filename",
    ["photo.gif", "photo.txt", "photo", "photo.PNG"],
)
def test_create_profile_rejects_invalid_picture_extension(
    filename: str,
) -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    expected_status = 201 if filename == "photo.PNG" else 422

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PATH,
            headers=profile_headers(),
            data=profile_data(),
            files=profile_files(filename),
        )

    assert response.status_code == expected_status
    if expected_status == 422:
        assert minio.uploads == []


def test_create_profile_rejects_empty_picture() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PATH,
            headers=profile_headers(),
            data=profile_data(),
            files={"profile_pic": ("photo.png", b"", "image/png")},
        )

    assert response.status_code == 422
    assert minio.uploads == []


def test_create_profile_rejects_picture_at_five_megabytes() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PATH,
            headers=profile_headers(),
            data=profile_data(),
            files={
                "profile_pic": (
                    "photo.png",
                    b"x" * PROFILE_PICTURE_MAX_BYTES,
                    "image/png",
                )
            },
        )

    assert response.status_code == 422
    assert minio.uploads == []


def test_create_profile_upload_failure_returns_503() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    minio = FakeMinio()
    minio.upload_error = RuntimeError("minio unavailable")
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PATH,
            headers=profile_headers(),
            data=profile_data(),
            files=profile_files(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": PROFILE_PICTURE_UNAVAILABLE}
    assert minio.uploads == []
    assert session.added == []
    assert session.commits == 0


def test_create_profile_commit_failure_cleans_up_uploaded_picture() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()
    minio = FakeMinio()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis, minio_client=minio) as client:
        response = client.post(
            PROFILE_PATH,
            headers=profile_headers(),
            data=profile_data(),
            files=profile_files(),
        )

    assert response.status_code == 500
    assert session.rollbacks == 1
    assert len(minio.uploads) == 1
    object_key = minio.uploads[0]["object_key"]
    assert (BUCKET, object_key) in minio.deleted
    assert (BUCKET, object_key) not in minio.objects
