"""Notification provider selection."""

from app.services.notification.exception import ProviderNotFoundError
from app.services.notification.provider.base import BaseProvider
from app.services.notification.provider.provider_registery import (
    ProviderType,
    provider_registery,
)


class ProviderManager:
    """Resolve the provider registered for a notification type."""

    @staticmethod
    def get_provider(
        provider_type: str | ProviderType,
    ) -> BaseProvider:
        """Return the provider for a type or raise ProviderNotFoundError."""

        try:
            provider_key = ProviderType(provider_type).value
        except ValueError as exc:
            raise ProviderNotFoundError(
                f"No provider is registered for {provider_type}"
            ) from exc

        try:
            return provider_registery[provider_key]
        except KeyError as exc:
            raise ProviderNotFoundError(
                f"No provider is registered for {provider_key}"
            ) from exc


__all__ = ["BaseProvider", "ProviderManager", "ProviderType"]
