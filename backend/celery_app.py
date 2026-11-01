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
TASK_MAX_DELIVERIES = 5

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

celery_app = Celery(
    "coursewave",
    broker=settings.rabbitmq_url,
    imports=("app.services.notification.tasks",),
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
    task_queues=(dead_letter_queue, task_queue),
    task_routes={
        "notification.process_notification": {
            "queue": TASK_QUEUE,
            "routing_key": TASK_QUEUE,
        },
    },
)


@worker_init.connect
def declare_queues(**_kwargs: Any) -> None:
    """Declare every configured queue before the worker starts consuming.

    The worker consumes only ``task``, so Celery would not otherwise declare
    ``task.dlq`` and RabbitMQ would drop dead-lettered messages that cannot be
    routed.
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


# ``celery -A celery_app`` discovers the app through this conventional name.
app = celery_app
