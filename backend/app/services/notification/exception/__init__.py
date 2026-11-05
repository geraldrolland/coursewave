"""Exceptions for the notification service."""


class NotificationServiceError(Exception):
    """Base exception for notification service errors."""


class NotificationError(NotificationServiceError):
    """Raised when a notification cannot be delivered."""


class ProviderError(NotificationError):
    """Base exception for notification provider errors."""


class ProviderNotFoundError(ProviderError):
    """Raised when no provider is registered for a notification type."""


class ProviderNotAvailableError(ProviderError):
    """Raised when a notification provider cannot deliver a message."""


__all__ = [
    "NotificationError",
    "NotificationServiceError",
    "ProviderError",
    "ProviderNotAvailableError",
    "ProviderNotFoundError",
]
