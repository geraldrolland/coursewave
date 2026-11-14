"""Instructor database model."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, JSON, Enum as SAEnum, func
from sqlmodel import Field, SQLModel

from app.base_utils import utc_now


class AuthMethod(str, Enum):
    """Supported instructor authentication methods."""

    EMAIL = "email"
    GOOGLE = "google"


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    """Return the stored values for a database enum."""

    return [str(member.value) for member in enum_cls]


class Instructor(SQLModel, table=True):
    """An instructor account."""

    __tablename__ = "instructor"

    instructor_id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(max_length=320, unique=True, index=True)
    role: str = Field(default="instructor", max_length=20)
    is_email_verified: bool = Field(default=False)
    is_2fa_enabled: bool = Field(default=False)
    password_hash: str | None = Field(default=None, max_length=255)
    profile_pic_url: str | None = Field(default=None, max_length=2048)
    cover_image_url: str | None = Field(default=None, max_length=2048)
    profile: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    auth_method: AuthMethod = Field(
        default=AuthMethod.EMAIL,
        sa_column=Column(
            SAEnum(
                AuthMethod,
                name="instructor_auth_method",
                values_callable=_enum_values,
            ),
            nullable=False,
            default=AuthMethod.EMAIL,
        ),
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


__all__ = ["AuthMethod", "Instructor"]
