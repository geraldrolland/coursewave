"""Instructor education database model."""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Enum as SAEnum, func
from sqlmodel import Field, SQLModel

from app.base_utils import utc_now


class Degree(str, Enum):
    """Supported education degrees."""

    DOCTORATE = "doctorate"
    MASTERS = "masters"
    BACHELOR = "bachelor"
    ASSOCIATE = "associate"
    DIPLOMA = "diploma"


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    """Return the stored values for a database enum."""

    return [str(member.value) for member in enum_cls]


class Education(SQLModel, table=True):
    """Education details for an instructor."""

    __tablename__ = "education"

    education_id: UUID = Field(default_factory=uuid4, primary_key=True)
    instructor_id: UUID = Field(
        foreign_key="instructor.instructor_id",
        unique=True,
        index=True,
    )
    school: str = Field(max_length=255)
    course: str = Field(max_length=255)
    degree: Degree = Field(
        sa_column=Column(
            SAEnum(
                Degree,
                name="education_degree",
                values_callable=_enum_values,
            ),
            nullable=False,
        ),
    )
    graduate_at: str = Field(max_length=7)
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


__all__ = ["Degree", "Education"]
