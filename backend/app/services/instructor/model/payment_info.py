"""Instructor payment information database model."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, func
from sqlmodel import Field, SQLModel

from app.base_utils import utc_now


class PaymentInfo(SQLModel, table=True):
    """Payment details for an instructor."""

    __tablename__ = "payment_info"

    payment_info_id: UUID = Field(default_factory=uuid4, primary_key=True)
    instructor_id: UUID = Field(
        foreign_key="instructor.instructor_id",
        unique=True,
        index=True,
    )
    full_name: str = Field(max_length=200)
    account_number: str = Field(max_length=64)
    bank: str = Field(max_length=255)
    version_num: int = Field(default=1)
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


__all__ = ["PaymentInfo"]
