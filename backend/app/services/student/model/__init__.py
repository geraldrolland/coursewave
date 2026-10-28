"""Database models for the student service."""

from app.services.student.model.outbox import Outbox, OutboxStatus
from app.services.student.model.student import Student

__all__ = ["Student", "Outbox", "OutboxStatus"]
