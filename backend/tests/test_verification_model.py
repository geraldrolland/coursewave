"""Tests for the verification database model."""

from uuid import UUID

from sqlalchemy import JSON, Text

from app.services.instructor.model import Instructor
from app.services.verification.model import Verification, VerificationStatus


def make_verification(instructor_id: UUID) -> Verification:
    """Build a verification record with all required fields."""

    return Verification(
        instructor_id=instructor_id,
        id_verification={"document": "id.png"},
        academic_verification={"degree": "bachelor"},
    )


def test_verification_defaults_and_constraints() -> None:
    instructor = Instructor(email="ada@example.com")
    verification = make_verification(instructor.instructor_id)
    table = Verification.__table__

    assert isinstance(verification.id, UUID)
    assert verification.id_verification == {"document": "id.png"}
    assert verification.academic_verification == {"degree": "bachelor"}
    assert verification.status is VerificationStatus.PENDING
    assert verification.verified_at is None
    assert verification.verified_by is None
    assert verification.rejected_reason is None
    assert table.c.id.primary_key is True
    assert table.c.instructor_id.nullable is False
    assert table.c.instructor_id.unique is not True
    assert table.c.id_verification.nullable is False
    assert table.c.academic_verification.nullable is False
    assert isinstance(table.c.id_verification.type, JSON)
    assert isinstance(table.c.academic_verification.type, JSON)
    assert table.c.status.nullable is False
    assert table.c.verified_at.nullable is True
    assert table.c.verified_by.nullable is True
    assert isinstance(table.c.rejected_reason.type, Text)
    assert table.c.rejected_reason.nullable is True
    assert table.c.created_at.nullable is False
    assert table.c.updated_at.nullable is False
    assert list(table.c.verified_by.foreign_keys) == []

    foreign_key = next(iter(table.c.instructor_id.foreign_keys))
    assert foreign_key.target_fullname == "instructor.instructor_id"


def test_verification_status_enum() -> None:
    table = Verification.__table__

    assert table.c.status.type.name == "verification_status"
    assert VerificationStatus.PENDING.value == "pending"
    assert VerificationStatus.VERIFIED.value == "verified"
    assert VerificationStatus.REJECTED.value == "rejected"
