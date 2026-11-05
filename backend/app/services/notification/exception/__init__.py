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
    """Base exception for notification provider availability failures."""


class ProviderServiceNotAvailableError(ProviderNotAvailableError):
    """Raised when a provider service such as SendGrid cannot be reached."""


# Preserve the spelling used in the provider contract.
ProviderServiceNotAvailbleError = ProviderServiceNotAvailableError


__all__ = [
    "NotificationError",
    "NotificationServiceError",
    "ProviderError",
    "ProviderNotAvailableError",
    "ProviderNotFoundError",
    "ProviderServiceNotAvailbleError",
    "ProviderServiceNotAvailableError",
]
