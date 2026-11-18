"""Tests for the instructor database models."""

from datetime import date
from uuid import UUID

from sqlalchemy import JSON, Date, Text

from app.services.instructor.model import (
    AuthMethod,
    DegreeType,
    Education,
    Gender,
    Instructor,
    InstructorDetail,
    InstructorVerificationStatus,
    Onboard,
    OnboardingStatus,
    PaymentInfo,
    ProfessionalInfo,
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
    assert instructor.profile_pic_url is None
    assert instructor.cover_image_url is None
    assert not hasattr(instructor, "profile")
    assert table.c.role.nullable is False
    assert table.c.email.nullable is False
    assert table.c.email.unique is True
    assert table.c.password_hash.nullable is True
    assert table.c.profile_pic_url.nullable is True
    assert table.c.profile_pic_url.type.length == 2048
    assert table.c.cover_image_url.nullable is True
    assert table.c.cover_image_url.type.length == 2048
    assert "profile" not in table.c
    assert table.c.auth_method.nullable is False
    assert table.c.auth_method.type.name == "instructor_auth_method"


def make_onboard(instructor_id: UUID) -> Onboard:
    """Build an onboarding record with its default fields."""

    return Onboard(instructor_id=instructor_id)


def make_instructor_detail(
    instructor_id: UUID,
) -> InstructorDetail:
    """Build an instructor detail with its default fields."""

    return InstructorDetail(instructor_id=instructor_id)


def make_professional_info(
    instructor_id: UUID,
    instructor_detail_id: UUID,
) -> ProfessionalInfo:
    """Build professional info with all required fields."""

    return ProfessionalInfo(
        instructor_id=instructor_id,
        instructor_detail_id=instructor_detail_id,
        first_name="Ada",
        last_name="Lovelace",
        gender=Gender.FEMALE,
        country_of_residence="United Kingdom",
        state="England",
        city="London",
        bio="Mathematician and writer.",
        headline="Software engineer",
        expertise=["tech", "science"],
        years_of_exp=10,
        current_occupation="Engineer",
        social_links=[
            {"linkedin": "https://www.linkedin.com/in/ada"},
        ],
    )


def make_education(
    instructor_id: UUID,
    instructor_detail_id: UUID,
) -> Education:
    """Build an education record with all required fields."""

    return Education(
        instructor_id=instructor_id,
        instructor_detail_id=instructor_detail_id,
        institution_name="University of London",
        degree_type=DegreeType.BACHELORS,
        field_of_study="Computer Science",
    )


def make_payment_info(instructor_id: UUID) -> PaymentInfo:
    """Build payment information with all required fields."""

    return PaymentInfo(
        instructor_id=instructor_id,
        full_name="Ada Lovelace",
        account_number="0123456789",
        bank="Example Bank",
    )


def test_onboard_defaults_and_constraints() -> None:
    instructor = Instructor(email="ada@example.com")
    onboard = make_onboard(instructor.instructor_id)
    table = Onboard.__table__

    assert isinstance(onboard.id, UUID)
    assert onboard.is_onboarded is False
    assert onboard.onboarding_status is OnboardingStatus.NOT_STARTED
    assert onboard.onboarding_step == 0
    assert onboard.onboarding_started_at is None
    assert onboard.onboarding_completed_at is None
    assert OnboardingStatus.NOT_STARTED.value == "not_started"
    assert OnboardingStatus.IN_PROGRESS.value == "in_progress"
    assert OnboardingStatus.COMPLETED.value == "completed"

    assert table.c.id.primary_key is True
    assert table.c.instructor_id.unique is True
    assert table.c.instructor_id.nullable is False
    assert table.c.instructor_id.index is True
    assert table.c.is_onboarded.nullable is False
    assert table.c.onboarding_status.nullable is False
    assert table.c.onboarding_status.type.name == "onboarding_status"
    assert table.c.onboarding_step.nullable is False
    assert table.c.onboarding_started_at.nullable is True
    assert table.c.onboarding_completed_at.nullable is True
    assert not hasattr(Instructor, "onboard")

    foreign_key = next(iter(table.c.instructor_id.foreign_keys))
    assert foreign_key.target_fullname == "instructor.instructor_id"


def test_instructor_detail_defaults_and_constraints() -> None:
    instructor = Instructor(email="ada@example.com")
    detail = make_instructor_detail(instructor.instructor_id)
    table = InstructorDetail.__table__

    assert isinstance(detail.id, UUID)
    assert detail.version == 1
    assert (
        detail.verification_status
        is InstructorVerificationStatus.PENDING_REVIEW
    )
    assert (
        InstructorVerificationStatus.PENDING_REVIEW.value
        == "pending_review"
    )
    assert (
        InstructorVerificationStatus.UNDER_REVIEW.value
        == "under_review"
    )
    assert InstructorVerificationStatus.REJECTED.value == "rejected"
    assert InstructorVerificationStatus.VERIFIED.value == "verified"

    assert table.c.id.primary_key is True
    assert table.c.instructor_id.unique is not True
    assert table.c.instructor_id.nullable is False
    assert table.c.instructor_id.index is True
    assert table.c.verification_status.nullable is False
    assert (
        table.c.verification_status.type.name
        == "instructor_detail_verification_status"
    )
    assert table.c.version.nullable is False

    for column_name in ("created_at", "updated_at"):
        assert table.c[column_name].nullable is False

    foreign_key = next(iter(table.c.instructor_id.foreign_keys))
    assert foreign_key.target_fullname == "instructor.instructor_id"


def test_professional_info_defaults_and_constraints() -> None:
    instructor = Instructor(email="ada@example.com")
    detail = make_instructor_detail(instructor.instructor_id)
    info = make_professional_info(instructor.instructor_id, detail.id)
    table = ProfessionalInfo.__table__

    assert isinstance(info.id, UUID)
    assert info.middle_name is None
    assert info.company is None
    assert info.gender is Gender.FEMALE
    assert Gender.MALE.value == "male"
    assert Gender.FEMALE.value == "female"
    assert info.expertise == ["tech", "science"]
    assert info.social_links == [
        {"linkedin": "https://www.linkedin.com/in/ada"},
    ]

    assert table.c.id.primary_key is True
    assert table.c.instructor_id.nullable is False
    assert table.c.instructor_id.index is True
    assert table.c.instructor_detail_id.unique is True
    assert table.c.instructor_detail_id.nullable is False
    assert table.c.instructor_detail_id.index is True
    assert table.c.gender.nullable is False
    assert table.c.gender.type.name == "professional_info_gender"
    assert table.c.middle_name.nullable is True
    assert table.c.company.nullable is True
    assert isinstance(table.c.expertise.type, JSON)
    assert isinstance(table.c.social_links.type, JSON)
    assert table.c.social_links.nullable is False

    for column_name in (
        "first_name",
        "last_name",
        "gender",
        "country_of_residence",
        "state",
        "city",
        "bio",
        "headline",
        "expertise",
        "years_of_exp",
        "current_occupation",
        "social_links",
        "created_at",
        "updated_at",
    ):
        assert table.c[column_name].nullable is False

    instructor_foreign_key = next(
        iter(table.c.instructor_id.foreign_keys)
    )
    assert (
        instructor_foreign_key.target_fullname
        == "instructor.instructor_id"
    )
    detail_foreign_key = next(
        iter(table.c.instructor_detail_id.foreign_keys)
    )
    assert detail_foreign_key.target_fullname == "instructor_detail.id"


def test_education_defaults_and_constraints() -> None:
    instructor = Instructor(email="ada@example.com")
    detail = make_instructor_detail(instructor.instructor_id)
    education = make_education(
        instructor.instructor_id,
        detail.id,
    )
    table = Education.__table__

    assert isinstance(education.id, UUID)
    assert education.degree_type is DegreeType.BACHELORS
    assert education.major is None
    assert education.minor is None
    assert education.start_date is None
    assert education.end_date is None
    assert education.is_currently_studying is False
    assert education.grade is None
    assert education.description is None

    assert DegreeType.HIGH_SCHOOL.value == "high_school"
    assert DegreeType.DIPLOMA.value == "diploma"
    assert DegreeType.ASSOCIATE.value == "associate"
    assert DegreeType.BACHELORS.value == "bachelors"
    assert (
        DegreeType.POSTGRADUATE_DIPLOMA.value
        == "postgraduate_diploma"
    )
    assert DegreeType.MASTERS.value == "masters"
    assert DegreeType.DOCTORATE.value == "doctorate"
    assert (
        DegreeType.PROFESSIONAL_CERTIFICATION.value
        == "professional_certification"
    )
    assert DegreeType.OTHER.value == "other"

    assert table.c.id.primary_key is True
    assert table.c.instructor_id.nullable is False
    assert table.c.instructor_id.index is True
    assert table.c.instructor_detail_id.unique is True
    assert table.c.instructor_detail_id.nullable is False
    assert table.c.instructor_detail_id.index is True
    assert table.c.degree_type.nullable is False
    assert table.c.degree_type.type.name == "education_degree_type"
    assert table.c.is_currently_studying.nullable is False
    for column_name in (
        "institution_name",
        "degree_type",
        "field_of_study",
        "is_currently_studying",
    ):
        assert table.c[column_name].nullable is False

    for column_name in (
        "major",
        "minor",
        "start_date",
        "end_date",
        "grade",
        "description",
    ):
        assert table.c[column_name].nullable is True

    assert isinstance(table.c.start_date.type, Date)
    assert isinstance(table.c.end_date.type, Date)
    assert isinstance(table.c.description.type, Text)

    education_with_dates = Education(
        instructor_id=instructor.instructor_id,
        instructor_detail_id=detail.id,
        institution_name="University of London",
        degree_type=DegreeType.MASTERS,
        field_of_study="Computer Science",
        start_date=date(2020, 9, 1),
        end_date=date(2022, 6, 30),
        is_currently_studying=True,
    )
    assert education_with_dates.start_date == date(2020, 9, 1)
    assert education_with_dates.end_date == date(2022, 6, 30)
    assert education_with_dates.is_currently_studying is True

    instructor_foreign_key = next(
        iter(table.c.instructor_id.foreign_keys)
    )
    assert (
        instructor_foreign_key.target_fullname
        == "instructor.instructor_id"
    )
    detail_foreign_key = next(
        iter(table.c.instructor_detail_id.foreign_keys)
    )
    assert detail_foreign_key.target_fullname == "instructor_detail.id"


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
