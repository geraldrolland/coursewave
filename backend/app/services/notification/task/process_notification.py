"""Celery task for delivering notifications."""

import asyncio
import logging
from typing import Any
from uuid import UUID

from celery import Task
from celery.exceptions import Reject
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.base_config import get_settings
from app.db_config import create_postgresql_engine
from app.redis_config import create_redis_client
from app.services.notification.exception import NotificationError
from app.services.notification.model import Notification, NotificationStatus
from app.services.notification.provider import ProviderManager
from celery_app import DEATH_HEADERS, TASK_MAX_RETRIES, celery_app

logger = logging.getLogger("notification_service.task.process_notification")

NOTIFICATION_LOCK_TTL_SECONDS = 120


def _preserved_headers(task: Task) -> dict[str, Any]:
    """Return custom headers that must survive a lock-contention retry."""

    headers = dict(getattr(task.request, "headers", None) or {})
    for name in DEATH_HEADERS:
        headers.pop(name, None)
    headers.pop("eta", None)
    headers.pop("expires", None)
    return headers


async def _release_lock(lock: Any) -> None:
    """Release a Redis lock without masking the task result."""

    try:
        await lock.release()
    except RedisError:
        logger.warning(
            "Failed to release notification lock %s",
            getattr(lock, "name", "unknown"),
        )


async def _process_notification(
    task: Task,
    notification_id: UUID,
    redis: Redis,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Acquire the process lock, deliver, and persist the notification state."""

    process_lock = redis.lock(
        f"notification:process:{notification_id}",
        timeout=NOTIFICATION_LOCK_TTL_SECONDS,
    )
    if not await process_lock.acquire(blocking=False):
        logger.info(
            "Notification %s is already processing; retrying in %s seconds",
            notification_id,
            NOTIFICATION_LOCK_TTL_SECONDS,
        )
        raise task.retry(
            countdown=NOTIFICATION_LOCK_TTL_SECONDS,
            headers=_preserved_headers(task),
        )

    try:
        async with session_factory() as session:
            notification = await session.get(Notification, notification_id)
            if notification is None:
                logger.warning("Notification %s does not exist", notification_id)
                return

            if notification.status != NotificationStatus.PENDING:
                logger.info(
                    "Notification %s is already %s",
                    notification_id,
                    notification.status.value,
                )
                return

            try:
                provider = ProviderManager.get_provider(notification.type)
                provider.process(notification)
            except NotificationError as exc:
                logger.warning(
                    "Notification %s delivery failed; rejecting so RabbitMQ "
                    "requeues it for the DLQ: %s",
                    notification_id,
                    exc,
                )
                raise Reject(str(exc), requeue=True) from exc

            notification.status = NotificationStatus.PROCESSED
            await session.commit()
            logger.info("Notification %s processed", notification_id)
    finally:
        await _release_lock(process_lock)


@celery_app.task(
    bind=True,
    name="notification.process_notification",
    max_retries=TASK_MAX_RETRIES,
    default_retry_delay=10,
    acks_late=True,
    reject_on_worker_lost=True,
    acks_on_failure_or_timeout=False,
)
def process_notification(self: Task, notification_id: str) -> None:
    """Process one notification by ID."""

    async def run() -> None:
        settings = get_settings()
        redis = create_redis_client(settings)
        engine = create_postgresql_engine()
        session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        try:
            await _process_notification(
                self,
                UUID(notification_id),
                redis,
                session_factory,
            )
        finally:
            await redis.aclose()
            await engine.dispose()
            create_postgresql_engine.cache_clear()

    asyncio.run(run())
