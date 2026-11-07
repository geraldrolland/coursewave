"""Consume terminal notification failures from the RabbitMQ DLQ.

Messages in ``task.dlq`` are retried twice through the TTL-backed
``task.retry`` queue. The third DLQ delivery is terminal and is handed to the
registered notification handler, which marks the notification as failed.
"""

import asyncio
import inspect
import logging
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable

from kombu import Connection, Consumer, Producer
from kombu.message import Message

from celery_app import (
    DEATH_HEADERS,
    DLQ_MAX_RETRIES,
    TASK_DLQ,
    TASK_RETRY,
    dead_letter_exchange,
    dead_letter_queue,
    declare_all_queues,
)
from app.base_config import get_settings
from dlq.handlers import HANDLERS, Handler, InvalidNotificationId

logger = logging.getLogger("notification_service.dlq.consumer")

RETRY_HEADER = "x-retries"
DLQ_RETRY_THROTTLE_SECONDS = 5


class MalformedMessage(ValueError):
    """Raised when a DLQ message cannot be handled safely."""


@dataclass(frozen=True)
class TaskCall:
    """A Celery task name and its decoded arguments."""

    name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


@dataclass(frozen=True)
class HandlerCall:
    """A handler plus the arguments bound to its signature."""

    handler: Handler
    arguments: dict[str, Any]


def _message_payload(message: Message) -> Any:
    """Return a decoded payload, falling back to ``body`` for simple doubles."""

    payload = getattr(message, "payload", None)
    if payload is None:
        payload = getattr(message, "body", None)
    return payload


def parse_task_call(message: Message) -> TaskCall:
    """Extract the task name and arguments from a Celery message."""

    headers = message.headers or {}
    body = _message_payload(message)
    task_name = headers.get("task")

    if not task_name and isinstance(body, dict):
        task_name = body.get("task")

    if not isinstance(task_name, str) or not task_name:
        raise MalformedMessage("message has no task name")

    if isinstance(body, (list, tuple)):
        if len(body) < 2 or len(body) > 3:
            raise MalformedMessage("protocol v2 body must have two or three parts")
        args, kwargs = body[0], body[1]
    elif isinstance(body, dict):
        args = body.get("args", ())
        kwargs = body.get("kwargs", {})
    else:
        raise MalformedMessage(f"unsupported message body: {type(body).__name__}")

    if not isinstance(args, (list, tuple)):
        raise MalformedMessage("task args must be a list or tuple")
    if not isinstance(kwargs, dict):
        raise MalformedMessage("task kwargs must be a mapping")

    return TaskCall(task_name, tuple(args), dict(kwargs))


def read_retry_count(headers: dict[str, Any] | None) -> int | None:
    """Read the custom ``x-retries`` header, if the message has one."""

    raw = (headers or {}).get(RETRY_HEADER)
    if raw is None:
        return None

    if isinstance(raw, bool):
        raise MalformedMessage(f"invalid {RETRY_HEADER} value: {raw!r}")

    try:
        retries = int(raw)
    except (TypeError, ValueError) as exc:
        raise MalformedMessage(f"invalid {RETRY_HEADER} value: {raw!r}") from exc

    if retries < 0:
        raise MalformedMessage(f"invalid {RETRY_HEADER} value: {raw!r}")

    return retries


def resolve_handler(call: TaskCall) -> HandlerCall:
    """Resolve and bind the handler for a task message."""

    handler = HANDLERS.get(call.name)
    if handler is None:
        raise MalformedMessage(f"no DLQ handler for task {call.name!r}")

    try:
        bound = inspect.signature(handler).bind(*call.args, **call.kwargs)
    except TypeError as exc:
        raise MalformedMessage(
            f"arguments for task {call.name!r} do not match its handler: {exc}"
        ) from exc

    return HandlerCall(handler, dict(bound.arguments))


async def invoke_handler(call: HandlerCall) -> None:
    """Invoke a bound handler, awaiting it when necessary."""

    result = call.handler(**call.arguments)
    if inspect.isawaitable(result):
        await result


def build_retry_headers(
    headers: dict[str, Any] | None,
    retries: int,
) -> dict[str, Any]:
    """Build headers for a republished retry without stale routing state."""

    cleaned = {
        key: value
        for key, value in (headers or {}).items()
        if key not in DEATH_HEADERS
        and key not in {"eta", "expires", "compression"}
    }
    cleaned["retries"] = 0
    cleaned[RETRY_HEADER] = retries
    return cleaned


def republish_for_retry(
    producer: Producer,
    message: Message,
    retries: int,
) -> None:
    """Publish a DLQ message into the TTL-backed retry queue."""

    producer.publish(
        _message_payload(message),
        exchange=dead_letter_exchange,
        routing_key=TASK_RETRY,
        serializer="json",
        headers=build_retry_headers(message.headers, retries),
        delivery_mode=2,
        declare=[dead_letter_exchange],
        retry=True,
    )


def _republish(
    message: Message,
    producer: Producer,
    retries: int,
    sleep: Callable[[float], None],
) -> None:
    try:
        republish_for_retry(producer, message, retries)
    except Exception:
        logger.exception(
            "Failed to publish DLQ message to %s; throttling before requeue",
            TASK_RETRY,
        )
        sleep(DLQ_RETRY_THROTTLE_SECONDS)
        message.reject(requeue=True)
        return

    logger.warning(
        "Retrying DLQ message %s: retry %s of %s",
        message.headers.get("id", "unknown"),
        retries,
        DLQ_MAX_RETRIES - 1,
    )
    message.ack()


def _run_handler(
    message: Message,
    call: HandlerCall,
    sleep: Callable[[float], None],
) -> None:
    try:
        asyncio.run(invoke_handler(call))
    except InvalidNotificationId as exc:
        logger.error("Discarding DLQ message with malformed task args: %s", exc)
        message.ack()
    except Exception:
        logger.exception(
            "Terminal DLQ handler failed; throttling before requeue"
        )
        sleep(DLQ_RETRY_THROTTLE_SECONDS)
        message.reject(requeue=True)
    else:
        logger.error(
            "DLQ retry limit reached for task %s; terminal handler completed",
            call.handler.__name__,
        )
        message.ack()


def handle_message(
    message: Message,
    producer: Producer,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Apply one DLQ delivery: retry, handle terminally, or discard poison."""

    if sleep is None:
        sleep = time.sleep

    try:
        call = parse_task_call(message)
        handler_call = resolve_handler(call)
        retries = read_retry_count(message.headers)
    except MalformedMessage as exc:
        logger.error("Discarding malformed DLQ message: %s", exc)
        message.ack()
        return

    if retries is None:
        _republish(message, producer, 1, sleep)
        return

    if retries + 1 >= DLQ_MAX_RETRIES:
        _run_handler(message, handler_call, sleep)
        return

    _republish(message, producer, retries + 1, sleep)


def create_connection() -> Connection:
    """Create a broker connection with publisher confirms enabled."""

    return Connection(
        get_settings().rabbitmq_url,
        heartbeat=60,
        transport_options={"confirm_publish": True},
    )


def handle_decode_error(message: Message, exc: Exception) -> None:
    """Acknowledge messages that cannot be decoded as JSON."""

    logger.error("Discarding undecodable DLQ message: %s", exc)
    message.ack()


def consume_forever() -> None:
    """Consume ``task.dlq`` until the process is interrupted."""

    declare_all_queues()

    with create_connection() as connection:
        producer = Producer(
            connection,
            exchange=dead_letter_exchange,
            auto_declare=False,
        )

        def on_message(_body: Any, message: Message) -> None:
            handle_message(message, producer)

        with Consumer(
            connection.default_channel,
            queues=[dead_letter_queue],
            callbacks=[on_message],
            on_decode_error=handle_decode_error,
            accept=["json"],
            prefetch_count=1,
        ):
            logger.info("Consuming %s", TASK_DLQ)
            while True:
                try:
                    connection.drain_events(timeout=1)
                except socket.timeout:
                    continue


def main() -> None:
    """Run the DLQ consumer as a standalone process."""

    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    consume_forever()


if __name__ == "__main__":
    main()
