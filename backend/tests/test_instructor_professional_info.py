"""Tests for instructor professional-info endpoints."""

from uuid import UUID, uuid4

import pytest

from app.middleware.auth import MISSING_AUTHORIZATION
from app.services.instructor.model import (
    Gender,
    Instructor,
    InstructorDetail,
    Onboard,
    OnboardingStatus,
    ProfessionalInfo,
)
from app.services.instructor.router.professional_info import (
    INSTRUCTOR_DETAIL_NOT_FOUND,
    ONBOARD_NOT_FOUND,
    PROFESSIONAL_INFO_ALREADY_EXISTS,
    PROFESSIONAL_INFO_NOT_FOUND,
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

PROFESSIONAL_INFO_PATH = "/instructor/api/v1/professional_info"


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


def profile_payload(**overrides: object) -> dict[str, object]:
    """Build a valid professional-info payload."""

    payload: dict[str, object] = {
        "first_name": "Ada",
        "middle_name": "Augusta",
        "last_name": "Lovelace",
        "gender": "female",
        "country_of_residence": "United Kingdom",
        "state": "England",
        "city": "London",
        "bio": "Mathematician and writer.",
        "headline": "Software engineer",
        "expertise": ["software engineering", "AI engineering"],
        "years_of_exp": 10,
        "current_occupation": "Engineer",
        "company": "CourseWave",
        "social_links": [
            {"linkedin": "https://www.linkedin.com/in/ada"},
            {"x": "https://x.com/ada"},
        ],
    }
    payload.update(overrides)
    return payload


def make_detail(instructor: Instructor) -> InstructorDetail:
    """Build an instructor detail record."""

    return InstructorDetail(instructor_id=instructor.instructor_id)


def make_professional_info(
    instructor_id: UUID,
    detail: InstructorDetail,
) -> ProfessionalInfo:
    """Build a professional-info model row."""

    payload = profile_payload()
    return ProfessionalInfo(
        instructor_id=instructor_id,
        instructor_detail_id=detail.id,
        first_name=str(payload["first_name"]),
        middle_name=str(payload["middle_name"]),
        last_name=str(payload["last_name"]),
        gender=Gender.FEMALE,
        country_of_residence=str(payload["country_of_residence"]),
        state=str(payload["state"]),
        city=str(payload["city"]),
        bio=str(payload["bio"]),
        headline=str(payload["headline"]),
        expertise=list(payload["expertise"]),  # type: ignore[arg-type]
        years_of_exp=int(payload["years_of_exp"]),  # type: ignore[arg-type]
        current_occupation=str(payload["current_occupation"]),
        company=str(payload["company"]),
        social_links=list(payload["social_links"]),  # type: ignore[arg-type]
    )


def test_create_professional_info_uses_latest_detail() -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    onboard = Onboard(instructor_id=instructor.instructor_id)
    session = FakeInstructorSession(
        instructor,
        detail=detail,
        onboard=onboard,
    )
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.post(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json=profile_payload(),
        )

    assert response.status_code == 200
    assert response.json() == profile_payload()
    professional_info = next(
        model
        for model in session.added
        if isinstance(model, ProfessionalInfo)
    )
    assert professional_info.instructor_id == instructor.instructor_id
    assert professional_info.instructor_detail_id == detail.id
    assert professional_info.gender is Gender.FEMALE
    assert professional_info.expertise == [
        "software engineering",
        "AI engineering",
    ]
    assert professional_info.social_links == profile_payload()["social_links"]
    assert onboard.onboarding_step == 1
    assert onboard.onboarding_status is OnboardingStatus.IN_PROGRESS
    assert onboard.is_onboarded is False
    assert session.commits == 1
    statements = [str(statement) for statement in session.executed]
    assert any(
        "onboard" in statement and "FOR UPDATE" in statement
        for statement in statements
    )


def test_create_professional_info_rejects_existing_record() -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    onboard = Onboard(instructor_id=instructor.instructor_id)
    session = FakeInstructorSession(
        instructor,
        detail=detail,
        onboard=onboard,
        professional_info=make_professional_info(
            instructor.instructor_id,
            detail,
        ),
    )
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.post(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json=profile_payload(),
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": PROFESSIONAL_INFO_ALREADY_EXISTS
    }
    assert session.added == []
    assert session.commits == 0
    assert onboard.onboarding_step == 0
    assert onboard.onboarding_status is OnboardingStatus.NOT_STARTED


def test_create_professional_info_requires_detail() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.post(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json=profile_payload(),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": INSTRUCTOR_DETAIL_NOT_FOUND}


def test_create_professional_info_requires_authentication() -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    session = FakeInstructorSession(instructor, detail=detail)
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.post(
            PROFESSIONAL_INFO_PATH,
            json=profile_payload(),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_AUTHORIZATION}


def test_create_professional_info_rejects_incomplete_session() -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    session = FakeInstructorSession(instructor, detail=detail)
    redis = FakeRedis()
    seed_session(
        redis,
        user={"email": instructor.email, "role": "instructor"},
    )

    with instructor_client(session, redis) as client:
        response = client.post(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json=profile_payload(),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_AUTH_SESSION}


def test_create_professional_info_returns_404_for_unknown_instructor() -> None:
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
        response = client.post(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json=profile_payload(),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": INSTRUCTOR_NOT_FOUND}


@pytest.mark.parametrize(
    "overrides",
    [
        {"first_name": None},
        {"last_name": None},
        {"gender": "MALE"},
        {"gender": "unknown"},
        {"country_of_residence": None},
        {"state": None},
        {"city": None},
        {"bio": None},
        {"bio": "x" * 1001},
        {"headline": None},
        {"expertise": []},
        {"expertise": ["software engineering", " "]},
        {"years_of_exp": 0},
        {"years_of_exp": -1},
        {"current_occupation": None},
        {"social_links": []},
        {"social_links": [{}]},
        {"social_links": [{"linkedin": "not-a-url"}]},
        {"extra": "field"},
    ],
)
def test_create_professional_info_rejects_invalid_payloads(
    overrides: dict[str, object],
) -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    session = FakeInstructorSession(instructor, detail=detail)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.post(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json=profile_payload(**overrides),
        )

    assert response.status_code == 422
    assert session.added == []
    assert session.commits == 0


def test_create_professional_info_commit_failure_rolls_back() -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    session = FakeInstructorSession(
        instructor,
        detail=detail,
        onboard=Onboard(instructor_id=instructor.instructor_id),
    )
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.post(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json=profile_payload(),
        )

    assert response.status_code == 500
    assert session.rollbacks == 1


def test_create_professional_info_requires_onboard_record() -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    session = FakeInstructorSession(instructor, detail=detail)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.post(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json=profile_payload(),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": ONBOARD_NOT_FOUND}
    assert session.added == []
    assert session.commits == 0


def test_create_professional_info_completes_onboarding_at_step_six() -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    onboard = Onboard(
        instructor_id=instructor.instructor_id,
        onboarding_step=5,
        onboarding_status=OnboardingStatus.NOT_STARTED,
    )
    session = FakeInstructorSession(
        instructor,
        detail=detail,
        onboard=onboard,
    )
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.post(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json=profile_payload(),
        )

    assert response.status_code == 200
    assert onboard.onboarding_step == 6
    assert onboard.onboarding_status is OnboardingStatus.COMPLETED
    assert onboard.is_onboarded is True
    assert session.commits == 1


@pytest.mark.parametrize(
    ("status", "step", "is_onboarded"),
    [
        (OnboardingStatus.IN_PROGRESS, 1, False),
        (OnboardingStatus.COMPLETED, 2, True),
    ],
)
def test_create_professional_info_preserves_existing_onboard_status(
    status: OnboardingStatus,
    step: int,
    is_onboarded: bool,
) -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    onboard = Onboard(
        instructor_id=instructor.instructor_id,
        onboarding_step=step,
        onboarding_status=status,
        is_onboarded=is_onboarded,
    )
    session = FakeInstructorSession(
        instructor,
        detail=detail,
        onboard=onboard,
    )
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.post(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json=profile_payload(),
        )

    assert response.status_code == 200
    assert onboard.onboarding_step == step + 1
    assert onboard.onboarding_status is status
    assert onboard.is_onboarded is is_onboarded
    assert session.commits == 1


def test_get_professional_info_returns_stored_record() -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    session = FakeInstructorSession(
        instructor,
        detail=detail,
        professional_info=make_professional_info(instructor.instructor_id, detail),
    )
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.get(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
        )

    assert response.status_code == 200
    assert response.json() == profile_payload()
    assert session.commits == 0


def test_get_professional_info_requires_detail() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.get(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": INSTRUCTOR_DETAIL_NOT_FOUND}


def test_get_professional_info_returns_404_when_not_set() -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    session = FakeInstructorSession(instructor, detail=detail)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.get(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": PROFESSIONAL_INFO_NOT_FOUND}


def test_update_professional_info_updates_only_supplied_fields() -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    professional_info = make_professional_info(instructor.instructor_id, detail)
    session = FakeInstructorSession(
        instructor,
        detail=detail,
        professional_info=professional_info,
    )
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json={"first_name": "Grace"},
        )

    expected = {**profile_payload(), "first_name": "Grace"}
    assert response.status_code == 200
    assert response.json() == expected
    assert professional_info.first_name == "Grace"
    assert professional_info.last_name == "Lovelace"
    assert session.commits == 1


def test_update_professional_info_updates_multiple_fields() -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    professional_info = make_professional_info(instructor.instructor_id, detail)
    session = FakeInstructorSession(
        instructor,
        detail=detail,
        professional_info=professional_info,
    )
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)
    updates = {
        "middle_name": "Byron",
        "gender": "male",
        "expertise": ["mathematics"],
        "years_of_exp": 12,
        "company": "Analytical Engines",
        "social_links": [{"linkedin": "https://www.linkedin.com/in/grace"}],
    }

    with instructor_client(session, redis) as client:
        response = client.patch(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json=updates,
        )

    expected = {**profile_payload(), **updates}
    assert response.status_code == 200
    assert response.json() == expected
    assert professional_info.gender is Gender.MALE
    assert professional_info.years_of_exp == 12
    assert session.commits == 1


def test_update_professional_info_empty_payload_returns_full_record() -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    session = FakeInstructorSession(
        instructor,
        detail=detail,
        professional_info=make_professional_info(instructor.instructor_id, detail),
    )
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json={},
        )

    assert response.status_code == 200
    assert response.json() == profile_payload()
    assert session.commits == 1


def test_update_professional_info_ignores_explicit_nulls() -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    session = FakeInstructorSession(
        instructor,
        detail=detail,
        professional_info=make_professional_info(instructor.instructor_id, detail),
    )
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json={"first_name": None, "company": None},
        )

    assert response.status_code == 200
    assert response.json() == profile_payload()


def test_update_professional_info_requires_detail() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json={"first_name": "Grace"},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": INSTRUCTOR_DETAIL_NOT_FOUND}


def test_update_professional_info_returns_404_when_not_set() -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    session = FakeInstructorSession(instructor, detail=detail)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json={"first_name": "Grace"},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": PROFESSIONAL_INFO_NOT_FOUND}


@pytest.mark.parametrize(
    "updates",
    [
        {"first_name": ""},
        {"gender": "MALE"},
        {"gender": "unknown"},
        {"bio": "x" * 1001},
        {"expertise": []},
        {"expertise": [" "]},
        {"years_of_exp": 0},
        {"years_of_exp": -1},
        {"social_links": []},
        {"social_links": [{}]},
        {"social_links": [{"linkedin": "not-a-url"}]},
        {"extra": "field"},
    ],
)
def test_update_professional_info_rejects_invalid_payloads(
    updates: dict[str, object],
) -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    session = FakeInstructorSession(
        instructor,
        detail=detail,
        professional_info=make_professional_info(instructor.instructor_id, detail),
    )
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json=updates,
        )

    assert response.status_code == 422
    assert session.commits == 0


def test_update_professional_info_commit_failure_rolls_back() -> None:
    instructor = make_instructor(password=None)
    detail = make_detail(instructor)
    session = FakeInstructorSession(
        instructor,
        detail=detail,
        professional_info=make_professional_info(instructor.instructor_id, detail),
    )
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            PROFESSIONAL_INFO_PATH,
            headers=profile_headers(),
            json={"first_name": "Grace"},
        )

    assert response.status_code == 500
    assert session.rollbacks == 1
