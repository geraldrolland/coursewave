"""Terminal handlers for notification tasks that exhaust DLQ retries."""

import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db_config import create_postgresql_engine
from app.services.notification.model import Notification, NotificationStatus

logger = logging.getLogger("notification_service.dlq.handlers")

Handler = Callable[..., Awaitable[None]]


class InvalidNotificationId(ValueError):
    """Raised when a DLQ task does not carry a valid notification UUID."""


async def _mark_notif_failed(
    notification_id: UUID,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Mark a pending notification as permanently failed."""

    async with session_factory() as session:
        notification = await session.get(
            Notification,
            notification_id,
            with_for_update=True,
        )
        if notification is None:
            logger.warning("Notification %s does not exist", notification_id)
            return

        if notification.status == NotificationStatus.PROCESSED:
            logger.info(
                "Notification %s was already processed; leaving it unchanged",
                notification_id,
            )
            return

        if notification.status == NotificationStatus.FAILED:
            logger.info("Notification %s is already failed", notification_id)
            return

        notification.status = NotificationStatus.FAILED
        await session.commit()
        logger.error(
            "Notification %s failed permanently after all DLQ retries",
            notification_id,
        )


async def mark_notif_failed(notification_id: UUID | str) -> None:
    """Handle a terminal task failure by marking its notification failed."""

    try:
        parsed_id = (
            notification_id
            if isinstance(notification_id, UUID)
            else UUID(str(notification_id))
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise InvalidNotificationId(
            f"invalid notification id: {notification_id!r}"
        ) from exc

    engine = create_postgresql_engine()
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    try:
        await _mark_notif_failed(parsed_id, session_factory)
    finally:
        await engine.dispose()
        create_postgresql_engine.cache_clear()


HANDLERS: dict[str, Handler] = {
    "notification.process_notification": mark_notif_failed,
    "process_notification": mark_notif_failed,
}

__all__ = [
    "HANDLERS",
    "Handler",
    "InvalidNotificationId",
    "mark_notif_failed",
]
