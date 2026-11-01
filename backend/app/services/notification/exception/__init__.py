"""Exceptions for the notification service."""


class NotificationServiceError(Exception):
    """Base exception for notification service errors."""


class NotificationError(NotificationServiceError):
    """Raised when a notification cannot be delivered."""


__all__ = ["NotificationError", "NotificationServiceError"]
