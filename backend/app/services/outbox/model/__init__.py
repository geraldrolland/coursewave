"""Database models for the outbox service."""

from app.services.outbox.model.outbox import Outbox, OutboxStatus

__all__ = ["Outbox", "OutboxStatus"]
