"""Transactional outbox database model."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, JSON, Enum as SAEnum
from sqlmodel import Field, SQLModel


class OutboxStatus(str, Enum):
    """Processing states for an outbox message."""

    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    """Return the stored values for a database enum."""

    return [str(member.value) for member in enum_cls]


class Outbox(SQLModel, table=True):
    """A task waiting to be dispatched by the outbox worker."""

    __tablename__ = "outbox"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    status: OutboxStatus = Field(
        default=OutboxStatus.PENDING,
        sa_column=Column(
            "status",
            SAEnum(
                OutboxStatus,
                name="outbox_status",
                values_callable=_enum_values,
            ),
            nullable=False,
            default=OutboxStatus.PENDING,
            index=True,
        ),
    )
    retry_after: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )
    num_of_retries: int = Field(default=0)
    payload: dict[str, Any] = Field(
        sa_column=Column(JSON, nullable=False),
    )
    table: str = Field(max_length=255)
    ref_id: str = Field(max_length=255)


__all__ = ["Outbox", "OutboxStatus"]
