"""Instructor education database model."""

from datetime import date, datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Enum as SAEnum,
    Text,
    func,
)
from sqlmodel import Field, SQLModel

from app.base_utils import utc_now


class DegreeType(str, Enum):
    """Supported education degree types."""

    HIGH_SCHOOL = "high_school"
    DIPLOMA = "diploma"
    ASSOCIATE = "associate"
    BACHELORS = "bachelors"
    POSTGRADUATE_DIPLOMA = "postgraduate_diploma"
    MASTERS = "masters"
    DOCTORATE = "doctorate"
    PROFESSIONAL_CERTIFICATION = "professional_certification"
    OTHER = "other"


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    """Return the stored values for a database enum."""

    return [str(member.value) for member in enum_cls]


class Education(SQLModel, table=True):
    """Education details for an instructor."""

    __tablename__ = "education"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    instructor_id: UUID = Field(
        foreign_key="instructor.instructor_id",
        index=True,
    )
    instructor_detail_id: UUID = Field(
        foreign_key="instructor_detail.id",
        unique=True,
        index=True,
    )
    institution_name: str = Field(max_length=255)
    degree_type: DegreeType = Field(
        sa_column=Column(
            SAEnum(
                DegreeType,
                name="education_degree_type",
                values_callable=_enum_values,
            ),
            nullable=False,
        ),
    )
    field_of_study: str = Field(max_length=255)
    major: str | None = Field(default=None, max_length=255)
    minor: str | None = Field(default=None, max_length=255)
    start_date: date | None = Field(
        default=None,
        sa_column=Column(Date, nullable=True),
    )
    end_date: date | None = Field(
        default=None,
        sa_column=Column(Date, nullable=True),
    )
    is_currently_studying: bool = Field(default=False)
    grade: str | None = Field(default=None, max_length=100)
    description: str | None = Field(
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


__all__ = ["DegreeType", "Education"]
