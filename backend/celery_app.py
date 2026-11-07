"""Celery application configuration for the notification service."""

from typing import Any

from celery import Celery
from celery.signals import worker_init
from kombu import Exchange, Queue
from kombu.utils.functional import retry_over_time

from app.base_config import get_settings

TASK_QUEUE = "task"
TASK_DLQ = "task.dlq"
TASK_DLX = "task.dlx"
TASK_RETRY = "task.retry"
TASK_MAX_DELIVERIES = 5
TASK_MAX_RETRIES = 5
DLQ_MAX_RETRIES = 3
TASK_RETRY_TTL_SECONDS = 300

# RabbitMQ adds these headers when it dead-letters a message. Republishing a
# message with stale death history can trigger the broker's cycle detection.
DEATH_HEADERS = (
    "x-death",
    "x-first-death-exchange",
    "x-first-death-queue",
    "x-first-death-reason",
    "x-last-death-exchange",
    "x-last-death-queue",
    "x-last-death-reason",
)

settings = get_settings()

task_exchange = Exchange(TASK_QUEUE, type="topic", durable=True)
dead_letter_exchange = Exchange(TASK_DLX, type="topic", durable=True)

# Declare the DLQ first so its exchange exists before the main queue references
# it as a dead-letter target.
dead_letter_queue = Queue(
    TASK_DLQ,
    exchange=dead_letter_exchange,
    routing_key=TASK_DLQ,
    durable=True,
    queue_arguments={
        "x-queue-type": "quorum",
        "x-delivery-limit": -1,
    },
)

task_queue = Queue(
    TASK_QUEUE,
    exchange=task_exchange,
    routing_key=TASK_QUEUE,
    durable=True,
    queue_arguments={
        "x-queue-type": "quorum",
        "x-delivery-limit": TASK_MAX_DELIVERIES,
        "x-dead-letter-exchange": TASK_DLX,
        "x-dead-letter-routing-key": TASK_DLQ,
    },
)

# The retry queue has no consumer. RabbitMQ holds each message for the TTL and
# then dead-letters it back to the main task exchange.
retry_queue = Queue(
    TASK_RETRY,
    exchange=dead_letter_exchange,
    routing_key=TASK_RETRY,
    durable=True,
    queue_arguments={
        "x-queue-type": "quorum",
        "x-message-ttl": TASK_RETRY_TTL_SECONDS * 1000,
        "x-dead-letter-exchange": TASK_QUEUE,
        "x-dead-letter-routing-key": TASK_QUEUE,
        "x-dead-letter-strategy": "at-least-once",
        "x-overflow": "reject-publish",
    },
)

celery_app = Celery(
    "coursewave",
    broker=settings.rabbitmq_url,
    imports=("app.services.notification.task.process_notification",),
)

celery_app.conf.update(
    broker_connection_retry_on_startup=True,
    broker_transport_options={"confirm_publish": True},
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_acks_on_failure_or_timeout=False,
    worker_prefetch_multiplier=1,
    task_default_queue=TASK_QUEUE,
    task_default_exchange=TASK_QUEUE,
    task_default_exchange_type="topic",
    task_default_routing_key=TASK_QUEUE,
    broker_native_delayed_delivery_queue_type="quorum",
    task_queues=(dead_letter_queue, task_queue, retry_queue),
    task_routes={
        "notification.process_notification": {
            "queue": TASK_QUEUE,
            "routing_key": TASK_QUEUE,
        },
    },
)


def declare_all_queues() -> None:
    """Declare every configured queue and exchange.

    The worker consumes only ``task``, so Celery would not otherwise declare
    ``task.dlq`` or ``task.retry``. The DLQ consumer uses this helper as well so
    it can start before the worker without losing dead-lettered messages.
    """

    def declare() -> None:
        with celery_app.connection_for_write() as connection:
            channel = connection.default_channel
            for queue in celery_app.amqp.queues.values():
                queue(channel).declare()

    retry_over_time(
        declare,
        catch=Exception,
        interval_start=1,
        interval_step=1,
        max_retries=5,
    )


@worker_init.connect
def declare_queues(**_kwargs: Any) -> None:
    """Declare the queue topology before the worker starts consuming."""

    declare_all_queues()


# ``celery -A celery_app`` discovers the app through this conventional name.
app = celery_app
