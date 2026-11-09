"""Celery tasks for the outbox service."""

from app.services.outbox.task.process_outbox import process_outbox

__all__ = ["process_outbox"]
