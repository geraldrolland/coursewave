"""Notification database model."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, JSON, Enum as SAEnum, func
from sqlmodel import Field, SQLModel

from app.base_utils import utc_now


class NotificationType(str, Enum):
    """Supported notification delivery channels."""

    EMAIL = "email"
    PUSH = "push"
    SMS = "sms"


class NotificationAudience(str, Enum):
    """Supported notification recipients."""

    STUDENT = "student"
    INSTRUCTOR = "instructor"
    ADMIN = "admin"


class NotificationStatus(str, Enum):
    """Processing states for a notification."""

    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    """Return the stored values for a database enum."""

    return [str(member.value) for member in enum_cls]


class Notification(SQLModel, table=True):
    """A notification waiting to be delivered."""

    __tablename__ = "notification"

    notification_id: UUID = Field(default_factory=uuid4, primary_key=True)
    type: NotificationType = Field(
        sa_column=Column(
            "type",
            SAEnum(
                NotificationType,
                name="notification_type",
                values_callable=_enum_values,
            ),
            nullable=False,
        ),
    )
    payload: dict[str, Any] = Field(
        sa_column=Column(JSON, nullable=False),
    )
    belongs_to: NotificationAudience = Field(
        sa_column=Column(
            SAEnum(
                NotificationAudience,
                name="notification_audience",
                values_callable=_enum_values,
            ),
            nullable=False,
        ),
    )
    user_id: UUID = Field(index=True)
    status: NotificationStatus = Field(
        default=NotificationStatus.PENDING,
        sa_column=Column(
            SAEnum(
                NotificationStatus,
                name="notification_status",
                values_callable=_enum_values,
            ),
            nullable=False,
            default=NotificationStatus.PENDING,
        ),
    )
    release_after: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            index=True,
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

    def send(self) -> None:
        """Deliver the notification.

        Delivery channels are not implemented yet, so this is a no-op.
        Implementations may raise :class:`NotificationError`.
        """

        return None
