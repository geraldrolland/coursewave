"""Transactional outbox database model."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, DateTime, Enum as SAEnum, func
from sqlmodel import Field, SQLModel

from app.services.student.model.base import utc_now


class OutboxStatus(str, Enum):
    """Processing states for an outbox message."""

    pending = "pending"
    processed = "processed"
    failed = "failed"


class Outbox(SQLModel, table=True):
    """An event waiting to be delivered to the message broker."""

    __tablename__ = "outbox"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    status: OutboxStatus = Field(
        default=OutboxStatus.pending,
        sa_column=Column(
            SAEnum(
                OutboxStatus,
                name="outbox_status",
                values_callable=lambda enum_cls: [
                    member.value for member in enum_cls
                ],
            ),
            nullable=False,
            default=OutboxStatus.pending,
        ),
    )
    retry_after: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    num_of_retries: int = Field(default=0)
    topic: str = Field(max_length=255)
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
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
