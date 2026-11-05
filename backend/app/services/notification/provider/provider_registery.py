"""Registry of notification delivery providers."""

from enum import Enum

from app.services.notification.provider.base import BaseProvider
from app.services.notification.provider.email_provider import EmailProvider
from app.services.notification.provider.push_provider import PushProvider
from app.services.notification.provider.sms_provider import SMSProvider


class ProviderType(str, Enum):
    """Supported notification provider types."""

    EMAIL = "email"
    PUSH = "push"
    SMS = "sms"


email_provider = EmailProvider()
push_provider = PushProvider()
sms_provider = SMSProvider()

provider_registery: dict[str, BaseProvider] = {
    "email": email_provider,
    "push": push_provider,
    "sms": sms_provider,
}

__all__ = [
    "ProviderType",
    "email_provider",
    "provider_registery",
    "push_provider",
    "sms_provider",
]
