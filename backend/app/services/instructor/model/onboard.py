"""Instructor onboarding database model."""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Enum as SAEnum
from sqlmodel import Field, SQLModel


class OnboardingStatus(str, Enum):
    """Supported onboarding states."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    """Return the stored values for a database enum."""

    return [str(member.value) for member in enum_cls]


class Onboard(SQLModel, table=True):
    """Onboarding progress for an instructor."""

    __tablename__ = "onboard"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    instructor_id: UUID = Field(
        foreign_key="instructor.instructor_id",
        unique=True,
        index=True,
    )
    is_onboarded: bool = Field(default=False)
    onboarding_status: OnboardingStatus = Field(
        default=OnboardingStatus.NOT_STARTED,
        sa_column=Column(
            SAEnum(
                OnboardingStatus,
                name="onboarding_status",
                values_callable=_enum_values,
            ),
            nullable=False,
            default=OnboardingStatus.NOT_STARTED,
        ),
    )
    onboarding_step: int = Field(default=0)
    onboarding_started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    onboarding_completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


__all__ = ["Onboard", "OnboardingStatus"]
