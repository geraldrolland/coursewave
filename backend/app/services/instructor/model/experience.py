"""Instructor work experience database model."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, func
from sqlmodel import Field, SQLModel

from app.base_utils import utc_now


class Experience(SQLModel, table=True):
    """A work experience entry for an instructor."""

    __tablename__ = "experience"

    experience_id: UUID = Field(default_factory=uuid4, primary_key=True)
    instructor_id: UUID = Field(
        foreign_key="instructor.instructor_id",
        index=True,
    )
    company_name: str = Field(max_length=255)
    started_at: str = Field(max_length=7)
    ended_at: str | None = Field(
        default=None,
        max_length=7,
    )
    till_present: bool = Field(default=False)
    role: str = Field(max_length=255)
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


__all__ = ["Experience"]
