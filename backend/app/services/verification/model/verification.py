"""Instructor verification database model."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, JSON, Text, Enum as SAEnum, func
from sqlmodel import Field, SQLModel

from app.base_utils import utc_now


class VerificationStatus(str, Enum):
    """Supported verification states."""

    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    """Return the stored values for a database enum."""

    return [str(member.value) for member in enum_cls]


class Verification(SQLModel, table=True):
    """Verification details for an instructor."""

    __tablename__ = "verification"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    instructor_id: UUID = Field(
        foreign_key="instructor.instructor_id",
        index=True,
    )
    id_verification: dict[str, Any] = Field(
        sa_column=Column(JSON, nullable=False),
    )
    academic_verification: dict[str, Any] = Field(
        sa_column=Column(JSON, nullable=False),
    )
    status: VerificationStatus = Field(
        default=VerificationStatus.PENDING,
        sa_column=Column(
            SAEnum(
                VerificationStatus,
                name="verification_status",
                values_callable=_enum_values,
            ),
            nullable=False,
            default=VerificationStatus.PENDING,
        ),
    )
    verified_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=True,
        ),
    )
    verified_by: UUID | None = Field(default=None)
    rejected_reason: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
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


__all__ = ["Verification", "VerificationStatus"]
