"""Instructor professional information database model."""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, JSON, Enum as SAEnum, func
from sqlmodel import Field, SQLModel

from app.base_utils import utc_now


class Gender(str, Enum):
    """Supported professional-info genders."""

    MALE = "male"
    FEMALE = "female"


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    """Return the stored values for a database enum."""

    return [str(member.value) for member in enum_cls]


class ProfessionalInfo(SQLModel, table=True):
    """Professional details for an instructor detail record."""

    __tablename__ = "professional_info"

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
    first_name: str = Field(max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str = Field(max_length=100)
    gender: Gender = Field(
        sa_column=Column(
            SAEnum(
                Gender,
                name="professional_info_gender",
                values_callable=_enum_values,
            ),
            nullable=False,
        ),
    )
    country_of_residence: str = Field(max_length=100)
    state: str = Field(max_length=100)
    city: str = Field(max_length=100)
    bio: str = Field(max_length=1000)
    headline: str = Field(max_length=255)
    expertise: list[str] = Field(
        sa_column=Column(JSON, nullable=False),
    )
    years_of_exp: int = Field()
    current_occupation: str = Field(max_length=255)
    company: str | None = Field(default=None, max_length=255)
    social_links: list[dict[str, str]] = Field(
        sa_column=Column(JSON, nullable=False),
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


__all__ = ["Gender", "ProfessionalInfo"]
