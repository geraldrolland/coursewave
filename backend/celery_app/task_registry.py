"""Registry of tasks that can be dispatched by the outbox worker."""

from collections.abc import Callable
from typing import Any

from app.services.notification.task import process_notification
from app.services.outbox.task.process_outbox import process_outbox

TaskCallable = Callable[..., Any]

TASK_REGISTRY: dict[str, TaskCallable] = {
    "process_notification": process_notification,
    "process_outbox": process_outbox,
}

TASKS = TASK_REGISTRY

__all__ = ["TASK_REGISTRY", "TASKS", "TaskCallable"]
