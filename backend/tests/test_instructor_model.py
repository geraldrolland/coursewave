"""Tests for the instructor database models."""

from uuid import UUID

from sqlalchemy import JSON

from app.services.instructor.model import (
    AuthMethod,
    Degree,
    Education,
    Experience,
    Gender,
    Instructor,
    InstructorProfile,
    PaymentInfo,
)


def make_profile(instructor_id: UUID) -> InstructorProfile:
    """Build an instructor profile with all required fields."""

    return InstructorProfile(
        instructor_id=instructor_id,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        gender=Gender.FEMALE,
        country_of_residence="United Kingdom",
        city="London",
        bio="Mathematician and writer.",
        headline="Software engineer",
        profile_pic_url="http://localhost:9000/profilepics/ada.png",
        expertise=["tech", "science"],
        years_of_exp=10,
        current_occupation="Engineer",
        company="CourseWave",
    )


def test_instructor_defaults_and_constraints() -> None:
    instructor = Instructor(
        email="ada@example.com",
        password_hash="hashed-password",
    )
    table = Instructor.__table__

    assert isinstance(instructor.instructor_id, UUID)
    assert instructor.is_email_verified is False
    assert instructor.is_2fa_enabled is False
    assert instructor.auth_method is AuthMethod.EMAIL
    assert instructor.role == "instructor"
    assert table.c.role.nullable is False
    assert table.c.email.nullable is False
    assert table.c.email.unique is True
    assert table.c.password_hash.nullable is True
    assert table.c.auth_method.nullable is False
    assert table.c.auth_method.type.name == "instructor_auth_method"


def test_instructor_profile_defaults_and_constraints() -> None:
    instructor = Instructor(
        email="ada@example.com",
        password_hash=None,
    )
    profile = make_profile(instructor.instructor_id)
    table = InstructorProfile.__table__

    assert isinstance(profile.profile_id, UUID)
    assert profile.middle_name is None
    assert profile.state is None
    assert profile.social_links is None
    assert profile.expertise == ["tech", "science"]
    assert profile.gender is Gender.FEMALE
    assert Gender.MALE.value == "male"
    assert Gender.FEMALE.value == "female"
    assert table.c.profile_id.primary_key is True
    assert table.c.instructor_id.unique is True
    assert table.c.instructor_id.nullable is False
    assert table.c.email.unique is True
    assert table.c.email.nullable is False
    assert table.c.gender.nullable is False
    assert table.c.gender.type.name == "instructor_gender"
    assert table.c.middle_name.nullable is True
    assert table.c.state.nullable is True
    assert table.c.company.nullable is True
    assert table.c.social_links.nullable is True
    assert table.c.bio.type.length == 1000
    assert isinstance(table.c.expertise.type, JSON)
    assert isinstance(table.c.social_links.type, JSON)
    for column_name in (
        "first_name",
        "last_name",
        "email",
        "gender",
        "country_of_residence",
        "city",
        "bio",
        "headline",
        "profile_pic_url",
        "expertise",
        "years_of_exp",
        "current_occupation",
    ):
        assert table.c[column_name].nullable is False

    foreign_key = next(iter(table.c.instructor_id.foreign_keys))
    assert foreign_key.target_fullname == "instructor.instructor_id"


def make_education(
    instructor_id: UUID,
    graduate_at: str = "09/2024",
) -> Education:
    """Build an education record with all required fields."""

    return Education(
        instructor_id=instructor_id,
        school="University of London",
        course="Computer Science",
        degree=Degree.BACHELOR,
        graduate_at=graduate_at,
    )


def make_experience(
    instructor_id: UUID,
    started_at: str = "09/2024",
    ended_at: str | None = None,
) -> Experience:
    """Build an experience record with all required fields."""

    return Experience(
        instructor_id=instructor_id,
        company_name="CourseWave",
        started_at=started_at,
        ended_at=ended_at,
        role="Software Engineer",
    )


def make_payment_info(instructor_id: UUID) -> PaymentInfo:
    """Build payment information with all required fields."""

    return PaymentInfo(
        instructor_id=instructor_id,
        full_name="Ada Lovelace",
        account_number="0123456789",
        bank="Example Bank",
    )


def test_education_defaults_and_constraints() -> None:
    instructor = Instructor(email="ada@example.com")
    education = make_education(instructor.instructor_id)
    table = Education.__table__

    assert isinstance(education.education_id, UUID)
    assert education.degree is Degree.BACHELOR
    assert education.graduate_at == "09/2024"
    assert Degree.DOCTORATE.value == "doctorate"
    assert Degree.MASTERS.value == "masters"
    assert Degree.BACHELOR.value == "bachelor"
    assert Degree.ASSOCIATE.value == "associate"
    assert Degree.DIPLOMA.value == "diploma"
    assert table.c.education_id.primary_key is True
    assert table.c.instructor_id.unique is True
    assert table.c.instructor_id.nullable is False
    assert table.c.degree.nullable is False
    assert table.c.degree.type.name == "education_degree"
    for column_name in ("school", "course", "degree", "graduate_at"):
        assert table.c[column_name].nullable is False

    foreign_key = next(iter(table.c.instructor_id.foreign_keys))
    assert foreign_key.target_fullname == "instructor.instructor_id"


def test_experience_defaults_and_constraints() -> None:
    instructor = Instructor(email="ada@example.com")
    experience = make_experience(instructor.instructor_id)
    table = Experience.__table__

    assert isinstance(experience.experience_id, UUID)
    assert experience.ended_at is None
    assert experience.till_present is False
    assert table.c.experience_id.primary_key is True
    assert table.c.instructor_id.unique is not True
    assert table.c.instructor_id.nullable is False
    assert table.c.ended_at.nullable is True
    assert table.c.till_present.nullable is False
    for column_name in ("company_name", "started_at", "role"):
        assert table.c[column_name].nullable is False

    foreign_key = next(iter(table.c.instructor_id.foreign_keys))
    assert foreign_key.target_fullname == "instructor.instructor_id"


def test_payment_info_defaults_and_constraints() -> None:
    instructor = Instructor(email="ada@example.com")
    payment_info = make_payment_info(instructor.instructor_id)
    table = PaymentInfo.__table__

    assert isinstance(payment_info.payment_info_id, UUID)
    assert payment_info.version_num == 1
    assert table.c.payment_info_id.primary_key is True
    assert table.c.instructor_id.unique is True
    assert table.c.instructor_id.nullable is False
    assert table.c.version_num.nullable is False
    for column_name in ("full_name", "account_number", "bank", "version_num"):
        assert table.c[column_name].nullable is False

    foreign_key = next(iter(table.c.instructor_id.foreign_keys))
    assert foreign_key.target_fullname == "instructor.instructor_id"
