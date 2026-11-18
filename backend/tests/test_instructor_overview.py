"""Tests for the instructor overview and 2FA endpoints."""

from uuid import uuid4

import pytest

from app.middleware.auth import MISSING_AUTHORIZATION
from app.services.instructor.model import (
    Instructor,
    InstructorDetail,
    InstructorVerificationStatus,
)
from app.services.instructor.router.instructor import (
    INSTRUCTOR_DETAIL_NOT_FOUND,
    TWO_FA_ALREADY_DISABLED,
    TWO_FA_ALREADY_ENABLED,
    TWO_FA_DISABLED,
    TWO_FA_ENABLED,
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
    instructor_client,
    make_instructor,
)

OVERVIEW_PATH = "/instructor/api/v1/"
TWO_FA_PATH = "/instructor/api/v1/2fa"


def overview_headers() -> dict[str, str]:
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


def make_detail(
    instructor: Instructor,
    version: int,
    verification_status: InstructorVerificationStatus,
) -> InstructorDetail:
    """Build an instructor detail with the requested version and status."""

    return InstructorDetail(
        instructor_id=instructor.instructor_id,
        version=version,
        verification_status=verification_status,
    )


def test_overview_prefers_under_review_over_higher_version_status() -> None:
    instructor = make_instructor(password=None)
    details = [
        make_detail(
            instructor,
            9,
            InstructorVerificationStatus.PENDING_REVIEW,
        ),
        make_detail(
            instructor,
            1,
            InstructorVerificationStatus.UNDER_REVIEW,
        ),
    ]
    session = FakeInstructorSession(instructor, details=details)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.get(
            OVERVIEW_PATH,
            headers=overview_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "instructor_id": str(instructor.instructor_id),
        "email": DEFAULT_EMAIL,
        "is_2fa_enabled": False,
        "profile_pic_url": None,
        "cover_image_url": None,
        "verification_status": "under_review",
    }
    assert session.commits == 0


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (
            [InstructorVerificationStatus.PENDING_REVIEW],
            "pending_review",
        ),
        (
            [InstructorVerificationStatus.VERIFIED],
            "verified",
        ),
        (
            [InstructorVerificationStatus.REJECTED],
            "rejected",
        ),
        (
            [
                InstructorVerificationStatus.REJECTED,
                InstructorVerificationStatus.VERIFIED,
            ],
            "verified",
        ),
    ],
)
def test_overview_status_fallbacks(
    statuses: list[InstructorVerificationStatus],
    expected: str,
) -> None:
    instructor = make_instructor(password=None)
    details = [
        make_detail(instructor, index + 1, status)
        for index, status in enumerate(statuses)
    ]
    session = FakeInstructorSession(instructor, details=details)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.get(
            OVERVIEW_PATH,
            headers=overview_headers(),
        )

    assert response.status_code == 200
    assert response.json()["verification_status"] == expected


def test_overview_returns_404_without_details() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.get(
            OVERVIEW_PATH,
            headers=overview_headers(),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": INSTRUCTOR_DETAIL_NOT_FOUND}


def test_overview_requires_authentication() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.get(OVERVIEW_PATH)

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_AUTHORIZATION}


def test_overview_rejects_incomplete_session() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_session(
        redis,
        user={"email": instructor.email, "role": "instructor"},
    )

    with instructor_client(session, redis) as client:
        response = client.get(
            OVERVIEW_PATH,
            headers=overview_headers(),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_AUTH_SESSION}


def test_overview_returns_404_for_unknown_instructor() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()
    seed_session(
        redis,
        user={
            "id": str(uuid4()),
            "email": DEFAULT_EMAIL,
            "role": "instructor",
        },
    )

    with instructor_client(session, redis) as client:
        response = client.get(
            OVERVIEW_PATH,
            headers=overview_headers(),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": INSTRUCTOR_NOT_FOUND}


@pytest.mark.parametrize(
    ("current", "requested", "message"),
    [
        (True, True, TWO_FA_ALREADY_ENABLED),
        (False, False, TWO_FA_ALREADY_DISABLED),
    ],
)
def test_2fa_unchanged_returns_already_message(
    current: bool,
    requested: bool,
    message: str,
) -> None:
    instructor = make_instructor(password=None)
    instructor.is_2fa_enabled = current
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            TWO_FA_PATH,
            headers=overview_headers(),
            json={"status": requested},
        )

    assert response.status_code == 200
    assert response.json() == {"message": message}
    assert instructor.is_2fa_enabled is current
    assert session.commits == 0


@pytest.mark.parametrize(
    ("current", "requested", "message"),
    [
        (False, True, TWO_FA_ENABLED),
        (True, False, TWO_FA_DISABLED),
    ],
)
def test_2fa_updates_status(
    current: bool,
    requested: bool,
    message: str,
) -> None:
    instructor = make_instructor(password=None)
    instructor.is_2fa_enabled = current
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            TWO_FA_PATH,
            headers=overview_headers(),
            json={"status": requested},
        )

    assert response.status_code == 200
    assert response.json() == {"message": message}
    assert instructor.is_2fa_enabled is requested
    assert session.commits == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"status": "true"},
        {"status": 1},
        {"status": True, "extra": "field"},
    ],
)
def test_2fa_rejects_invalid_payloads(payload: dict[str, object]) -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            TWO_FA_PATH,
            headers=overview_headers(),
            json=payload,
        )

    assert response.status_code == 422
    assert session.commits == 0


def test_2fa_requires_authentication() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.patch(
            TWO_FA_PATH,
            json={"status": True},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_AUTHORIZATION}


def test_2fa_returns_404_for_unknown_instructor() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()
    seed_session(
        redis,
        user={
            "id": str(uuid4()),
            "email": DEFAULT_EMAIL,
            "role": "instructor",
        },
    )

    with instructor_client(session, redis) as client:
        response = client.patch(
            TWO_FA_PATH,
            headers=overview_headers(),
            json={"status": True},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": INSTRUCTOR_NOT_FOUND}


def test_2fa_commit_failure_rolls_back() -> None:
    instructor = make_instructor(password=None)
    instructor.is_2fa_enabled = False
    session = FakeInstructorSession(instructor)
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            TWO_FA_PATH,
            headers=overview_headers(),
            json={"status": True},
        )

    assert response.status_code == 500
    assert session.rollbacks == 1
