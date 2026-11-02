"""Celery tasks for the notification service."""

from app.services.notification.task.process_notification import process_notification

__all__ = ["process_notification"]
