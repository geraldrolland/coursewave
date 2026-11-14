"""Tests for the instructor JSON profile endpoint."""

from uuid import uuid4

import pytest

from app.middleware.auth import MISSING_AUTHORIZATION
from app.services.instructor.model import Instructor
from app.services.instructor.router.instructor import (
    PROFILE_ALREADY_EXISTS,
    PROFILE_NOT_FOUND,
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

PROFILE_PATH = "/instructor/api/v1/profile"


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
    """Build a valid instructor profile payload."""

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


def test_create_profile_returns_and_stores_payload() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.post(
            PROFILE_PATH,
            headers=profile_headers(),
            json=profile_payload(),
        )

    assert response.status_code == 200
    assert response.json() == profile_payload()
    assert instructor.profile == profile_payload()
    assert session.commits == 1


def test_create_profile_supports_optional_fields_and_only_one_social_link() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)
    payload = profile_payload(
        middle_name=None,
        company=None,
        expertise=["AI engineering"],
        social_links=[{"linkedin": "https://www.linkedin.com/in/ada"}],
    )
    payload.pop("middle_name")
    payload.pop("company")

    with instructor_client(session, redis) as client:
        response = client.post(
            PROFILE_PATH,
            headers=profile_headers(),
            json=payload,
        )

    assert response.status_code == 200
    assert instructor.profile is not None
    assert instructor.profile["first_name"] == "Ada"
    assert "middle_name" not in instructor.profile
    assert "company" not in instructor.profile
    assert instructor.profile["social_links"] == [
        {"linkedin": "https://www.linkedin.com/in/ada"}
    ]
    assert session.commits == 1


def test_create_profile_rejects_existing_profile() -> None:
    instructor = make_instructor(password=None)
    instructor.profile = {"first_name": "Existing"}
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.post(
            PROFILE_PATH,
            headers=profile_headers(),
            json=profile_payload(),
        )

    assert response.status_code == 403
    assert response.json() == {"detail": PROFILE_ALREADY_EXISTS}
    assert session.commits == 0


def test_get_profile_returns_stored_profile() -> None:
    instructor = make_instructor(password=None)
    instructor.profile = profile_payload()
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.get(
            PROFILE_PATH,
            headers=profile_headers(),
        )

    assert response.status_code == 200
    assert response.json() == profile_payload()
    assert session.commits == 0


def test_get_profile_returns_404_when_profile_not_set() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.get(
            PROFILE_PATH,
            headers=profile_headers(),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": PROFILE_NOT_FOUND}
    assert session.commits == 0


def test_get_profile_requires_authentication() -> None:
    instructor = make_instructor(password=None)
    instructor.profile = profile_payload()
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.get(PROFILE_PATH)

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_AUTHORIZATION}


def test_get_profile_rejects_incomplete_session() -> None:
    instructor = make_instructor(password=None)
    instructor.profile = profile_payload()
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_session(
        redis,
        user={"email": instructor.email, "role": "instructor"},
    )

    with instructor_client(session, redis) as client:
        response = client.get(
            PROFILE_PATH,
            headers=profile_headers(),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_AUTH_SESSION}


def test_get_profile_returns_404_for_unknown_instructor() -> None:
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
            PROFILE_PATH,
            headers=profile_headers(),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": INSTRUCTOR_NOT_FOUND}


def test_update_profile_updates_only_supplied_fields() -> None:
    instructor = make_instructor(password=None)
    instructor.profile = profile_payload()
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            json={"first_name": "Grace"},
        )

    expected = {**profile_payload(), "first_name": "Grace"}
    assert response.status_code == 200
    assert response.json() == expected
    assert instructor.profile == expected
    assert session.commits == 1


def test_update_profile_updates_multiple_fields() -> None:
    instructor = make_instructor(password=None)
    instructor.profile = profile_payload()
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)
    updates = {
        "middle_name": "Byron",
        "expertise": ["mathematics"],
        "years_of_exp": 12,
        "company": "Analytical Engines",
        "social_links": [{"linkedin": "https://www.linkedin.com/in/grace"}],
    }

    with instructor_client(session, redis) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            json=updates,
        )

    expected = {**profile_payload(), **updates}
    assert response.status_code == 200
    assert response.json() == expected
    assert instructor.profile == expected
    assert session.commits == 1


def test_update_profile_empty_payload_returns_full_profile() -> None:
    instructor = make_instructor(password=None)
    instructor.profile = profile_payload()
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            json={},
        )

    assert response.status_code == 200
    assert response.json() == profile_payload()
    assert instructor.profile == profile_payload()
    assert session.commits == 1


def test_update_profile_ignores_explicit_nulls() -> None:
    instructor = make_instructor(password=None)
    instructor.profile = profile_payload()
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            json={"first_name": None, "company": None},
        )

    assert response.status_code == 200
    assert response.json() == profile_payload()
    assert instructor.profile == profile_payload()


def test_update_profile_requires_existing_profile() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            json={"first_name": "Grace"},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": PROFILE_NOT_FOUND}
    assert session.commits == 0


def test_update_profile_requires_authentication() -> None:
    instructor = make_instructor(password=None)
    instructor.profile = profile_payload()
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.patch(
            PROFILE_PATH,
            json={"first_name": "Grace"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_AUTHORIZATION}


def test_update_profile_returns_404_for_unknown_instructor() -> None:
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
            PROFILE_PATH,
            headers=profile_headers(),
            json={"first_name": "Grace"},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": INSTRUCTOR_NOT_FOUND}


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
def test_update_profile_rejects_invalid_payloads(
    updates: dict[str, object],
) -> None:
    instructor = make_instructor(password=None)
    instructor.profile = profile_payload()
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            json=updates,
        )

    assert response.status_code == 422
    assert session.commits == 0


def test_update_profile_commit_failure_rolls_back() -> None:
    instructor = make_instructor(password=None)
    instructor.profile = profile_payload()
    session = FakeInstructorSession(instructor)
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.patch(
            PROFILE_PATH,
            headers=profile_headers(),
            json={"first_name": "Grace"},
        )

    assert response.status_code == 500
    assert session.rollbacks == 1


def test_create_profile_requires_authentication() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.post(PROFILE_PATH, json=profile_payload())

    assert response.status_code == 401
    assert response.json() == {"detail": MISSING_AUTHORIZATION}
    assert session.commits == 0


def test_create_profile_rejects_incomplete_session() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_session(
        redis,
        user={"email": instructor.email, "role": "instructor"},
    )

    with instructor_client(session, redis) as client:
        response = client.post(
            PROFILE_PATH,
            headers=profile_headers(),
            json=profile_payload(),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_AUTH_SESSION}


def test_create_profile_returns_404_for_unknown_instructor() -> None:
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
            PROFILE_PATH,
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
def test_create_profile_rejects_invalid_payloads(
    overrides: dict[str, object],
) -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.post(
            PROFILE_PATH,
            headers=profile_headers(),
            json=profile_payload(**overrides),
        )

    assert response.status_code == 422
    assert session.commits == 0


def test_create_profile_commit_failure_rolls_back() -> None:
    instructor = make_instructor(password=None)
    session = FakeInstructorSession(instructor)
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()
    seed_instructor_session(redis, instructor)

    with instructor_client(session, redis) as client:
        response = client.post(
            PROFILE_PATH,
            headers=profile_headers(),
            json=profile_payload(),
        )

    assert response.status_code == 500
    assert session.rollbacks == 1
