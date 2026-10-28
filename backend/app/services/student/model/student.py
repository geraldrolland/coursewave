"""Student database model."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, func
from sqlmodel import Field, SQLModel

from app.services.student.model.base import utc_now


class Student(SQLModel, table=True):
    """A registered student account."""

    __tablename__ = "student"

    user_id: UUID = Field(default_factory=uuid4, primary_key=True)
    first_name: str = Field(max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str = Field(max_length=100)
    password_hash: str = Field(max_length=255)
    email: str = Field(max_length=320, unique=True, index=True)
    is_email_verified: bool = Field(default=False)
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
    profile_pic_url: str | None = Field(default=None, max_length=2048)
