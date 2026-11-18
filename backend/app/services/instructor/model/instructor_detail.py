"""Instructor detail database model."""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Enum as SAEnum, func
from sqlmodel import Field, SQLModel

from app.base_utils import utc_now


class InstructorVerificationStatus(str, Enum):
    """Supported instructor detail verification states."""

    PENDING_REVIEW = "pending_review"
    UNDER_REVIEW = "under_review"
    REJECTED = "rejected"
    VERIFIED = "verified"


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    """Return the stored values for a database enum."""

    return [str(member.value) for member in enum_cls]


class InstructorDetail(SQLModel, table=True):
    """Verification and version details for an instructor."""

    __tablename__ = "instructor_detail"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    instructor_id: UUID = Field(
        foreign_key="instructor.instructor_id",
        index=True,
    )
    verification_status: InstructorVerificationStatus = Field(
        default=InstructorVerificationStatus.PENDING_REVIEW,
        sa_column=Column(
            SAEnum(
                InstructorVerificationStatus,
                name="instructor_detail_verification_status",
                values_callable=_enum_values,
            ),
            nullable=False,
            default=InstructorVerificationStatus.PENDING_REVIEW,
        ),
    )
    version: int = Field(default=1)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
    )


__all__ = ["InstructorDetail", "InstructorVerificationStatus"]
