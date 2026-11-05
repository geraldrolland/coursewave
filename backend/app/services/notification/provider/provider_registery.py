"""Registry of notification delivery providers."""

from enum import Enum

from app.services.notification.provider.base import BaseProvider
from app.services.notification.provider.email_provider import EmailProvider


class ProviderType(str, Enum):
    """Supported notification provider types."""

    EMAIL = "email"
    PUSH = "push"
    SMS = "sms"


email_provider = EmailProvider()

provider_registery: dict[str, BaseProvider] = {
    "email": email_provider,
}

__all__ = ["ProviderType", "email_provider", "provider_registery"]
