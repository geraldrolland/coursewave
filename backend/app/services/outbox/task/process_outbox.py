"""Celery Beat task that dispatches pending outbox messages."""

import asyncio
import inspect
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.base_config import get_settings
from app.base_utils import utc_now
from app.db_config import create_postgresql_engine
from app.redis_config import create_redis_client
from app.services.notification.model import Notification, NotificationStatus
from app.services.outbox.model import Outbox, OutboxStatus
from celery_app import celery_app

logger = logging.getLogger("notification_service.outbox.task.process_outbox")

OUTBOX_BATCH_SIZE = 50
OUTBOX_MAX_RETRIES = 5
OUTBOX_RETRY_BASE_SECONDS = 10
OUTBOX_LOCK_TTL_SECONDS = 60
OUTBOX_TASK_NAME = "outbox.process_outbox"

TaskCallable = Callable[..., Any]
TaskRegistry = dict[str, TaskCallable]
TableModels = dict[str, type[Any]]

TABLE_MODELS: TableModels = {"notification": Notification}


class OutboxPayloadError(ValueError):
    """Raised when an outbox payload cannot be dispatched."""


def parse_outbox_payload(payload: Any) -> tuple[str, dict[str, Any]]:
    """Extract the task name and keyword arguments from an outbox payload."""

    if not isinstance(payload, dict):
        raise OutboxPayloadError("outbox payload must be a mapping")

    task_name = payload.get("task_name")
    arguments = payload.get("argument")

    if not isinstance(task_name, str) or not task_name:
        raise OutboxPayloadError("outbox payload has no task name")
    if not isinstance(arguments, dict):
        raise OutboxPayloadError("outbox payload argument must be a mapping")

    return task_name, dict(arguments)


def _get_task_registry() -> TaskRegistry:
    """Import the task registry lazily to avoid package import cycles."""

    from celery_app.task_registry import TASK_REGISTRY

    return TASK_REGISTRY


async def invoke_task(task: TaskCallable, arguments: dict[str, Any]) -> None:
    """Invoke a registered task without blocking the dispatch event loop."""

    result = await asyncio.to_thread(task, **arguments)
    if inspect.isawaitable(result):
        await result


async def _release_lock(lock: Any) -> None:
    """Release an outbox lock without masking the task result."""

    try:
        await lock.release()
    except RedisError:
        logger.warning(
            "Failed to release outbox lock %s",
            getattr(lock, "name", "unknown"),
        )


async def load_due_outbox_ids(
    session_factory: async_sessionmaker[AsyncSession],
    limit: int = OUTBOX_BATCH_SIZE,
) -> list[UUID]:
    """Return IDs for pending outbox rows whose retry time has arrived."""

    now = utc_now()
    async with session_factory() as session:
        result = await session.execute(
            select(Outbox.id)
            .where(
                Outbox.status == OutboxStatus.PENDING,
                or_(
                    Outbox.retry_after.is_(None),
                    Outbox.retry_after < now,
                ),
            )
            .order_by(Outbox.id)
            .limit(limit)
        )
        return list(result.scalars().all())


async def _mark_referenced_failed(
    session: AsyncSession,
    outbox: Outbox,
) -> None:
    """Mark the row referenced by an outbox message as failed."""

    model = TABLE_MODELS.get(outbox.table)
    if model is not Notification:
        logger.warning(
            "No outbox failure handler is registered for table %r",
            outbox.table,
        )
        return

    try:
        reference_id = UUID(outbox.ref_id)
    except (AttributeError, TypeError, ValueError):
        logger.warning(
            "Outbox message %s has an invalid ref_id %r",
            outbox.id,
            outbox.ref_id,
        )
        return

    notification = await session.get(
        Notification,
        reference_id,
        with_for_update=True,
    )
    if notification is None:
        logger.warning("Notification %s does not exist", reference_id)
        return

    if notification.status == NotificationStatus.PROCESSED:
        logger.info(
            "Notification %s was already processed; leaving it unchanged",
            reference_id,
        )
        return

    if notification.status == NotificationStatus.FAILED:
        logger.info("Notification %s is already failed", reference_id)
        return

    notification.status = NotificationStatus.FAILED
    logger.error(
        "Notification %s failed permanently after all outbox retries",
        reference_id,
    )


async def record_outbox_failure(
    session: AsyncSession,
    outbox: Outbox,
) -> None:
    """Apply the outbox retry budget and commit the resulting state."""

    attempt = outbox.num_of_retries + 1

    if attempt >= OUTBOX_MAX_RETRIES:
        outbox.num_of_retries = OUTBOX_MAX_RETRIES
        outbox.retry_after = None
        outbox.status = OutboxStatus.FAILED

        try:
            await _mark_referenced_failed(session, outbox)
        except Exception:
            logger.exception(
                "Failed to mark the referenced row for outbox %s failed",
                outbox.id,
            )

        logger.error(
            "Outbox message %s failed permanently after %s attempts",
            outbox.id,
            attempt,
        )
    else:
        outbox.num_of_retries = attempt
        outbox.retry_after = utc_now() + timedelta(
            seconds=OUTBOX_RETRY_BASE_SECONDS * attempt
        )
        logger.warning(
            "Outbox message %s failed; retry %s of %s scheduled in %s seconds",
            outbox.id,
            attempt,
            OUTBOX_MAX_RETRIES,
            OUTBOX_RETRY_BASE_SECONDS * attempt,
        )

    await session.commit()


async def process_outbox_row(
    outbox_id: UUID,
    redis: Redis,
    session_factory: async_sessionmaker[AsyncSession],
    task_registry: TaskRegistry | None = None,
) -> bool:
    """Dispatch one outbox message and persist its outcome."""

    lock = redis.lock(
        f"outbox:process:{outbox_id}",
        timeout=OUTBOX_LOCK_TTL_SECONDS,
    )
    try:
        acquired = await lock.acquire(blocking=False)
    except RedisError:
        logger.exception("Failed to acquire outbox lock for %s", outbox_id)
        return False

    if not acquired:
        logger.info("Outbox message %s is already processing", outbox_id)
        return False

    try:
        async with session_factory() as session:
            outbox = await session.get(
                Outbox,
                outbox_id,
                with_for_update={"skip_locked": True},
            )
            if outbox is None:
                logger.info("Outbox message %s is locked or missing", outbox_id)
                return False

            if outbox.status != OutboxStatus.PENDING:
                logger.info(
                    "Outbox message %s is already %s",
                    outbox_id,
                    outbox.status.value,
                )
                return False

            now = utc_now()
            if outbox.retry_after is not None and outbox.retry_after > now:
                logger.info(
                    "Outbox message %s is not due until %s",
                    outbox_id,
                    outbox.retry_after,
                )
                return False

            registry = (
                task_registry
                if task_registry is not None
                else _get_task_registry()
            )

            try:
                task_name, arguments = parse_outbox_payload(outbox.payload)
                task = registry.get(task_name)
                if task is None:
                    raise OutboxPayloadError(
                        f"no outbox task is registered for {task_name!r}"
                    )
                await invoke_task(task, arguments)
            except Exception:
                logger.exception("Outbox message %s failed", outbox_id)
                await record_outbox_failure(session, outbox)
                return False

            outbox.status = OutboxStatus.PROCESSED
            outbox.retry_after = None
            await session.commit()
            logger.info("Outbox message %s processed", outbox_id)
            return True
    finally:
        await _release_lock(lock)


async def process_pending_outbox(
    redis: Redis,
    session_factory: async_sessionmaker[AsyncSession],
    task_registry: TaskRegistry | None = None,
    limit: int = OUTBOX_BATCH_SIZE,
) -> int:
    """Process one batch of due outbox messages."""

    outbox_ids = await load_due_outbox_ids(session_factory, limit=limit)
    processed = 0

    for outbox_id in outbox_ids:
        try:
            if await process_outbox_row(
                outbox_id,
                redis,
                session_factory,
                task_registry,
            ):
                processed += 1
        except Exception:
            logger.exception(
                "Unexpected error processing outbox message %s",
                outbox_id,
            )

    return processed


@celery_app.task(name=OUTBOX_TASK_NAME)
def process_outbox() -> None:
    """Dispatch due outbox messages in batches."""

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
            processed = await process_pending_outbox(redis, session_factory)
            logger.info("Outbox batch complete: %s messages processed", processed)
        finally:
            await redis.aclose()
            await engine.dispose()
            create_postgresql_engine.cache_clear()

    asyncio.run(run())


__all__ = [
    "OUTBOX_BATCH_SIZE",
    "OUTBOX_LOCK_TTL_SECONDS",
    "OUTBOX_MAX_RETRIES",
    "OUTBOX_RETRY_BASE_SECONDS",
    "OUTBOX_TASK_NAME",
    "OutboxPayloadError",
    "TABLE_MODELS",
    "invoke_task",
    "load_due_outbox_ids",
    "parse_outbox_payload",
    "process_outbox",
    "process_outbox_row",
    "process_pending_outbox",
    "record_outbox_failure",
]
