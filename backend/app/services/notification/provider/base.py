"""Base class for notification delivery providers."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.notification.model import Notification


class BaseProvider(ABC):
    """Interface implemented by every notification provider."""

    @abstractmethod
    def process(self, notification: "Notification") -> None:
        """Deliver one notification through this provider."""
