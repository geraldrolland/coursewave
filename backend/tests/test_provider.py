"""Tests for notification providers."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.notification.exception import (
    ProviderNotAvailableError,
    ProviderNotFoundError,
)
from app.services.notification.model import (
    Notification,
    NotificationAudience,
    NotificationType,
)
from app.services.notification.provider import ProviderManager, ProviderType
from app.services.notification.provider.base import BaseProvider
from app.services.notification.provider.email_provider import (
    EMAIL_SUBJECT_REGISTRY,
    EmailProvider,
)
from app.services.notification.provider.provider_registery import provider_registery


def make_notification(payload: dict) -> Notification:
    """Build an email notification with a predictable audience."""

    return Notification(
        type=NotificationType.EMAIL,
        payload=payload,
        belongs_to=NotificationAudience.STUDENT,
        user_id=uuid4(),
    )


def test_provider_registry_contains_email_provider() -> None:
    provider = provider_registery["email"]

    assert isinstance(provider, EmailProvider)
    assert isinstance(provider, BaseProvider)


def test_provider_manager_returns_registered_email_provider() -> None:
    manager = ProviderManager()

    assert manager.get_provider("email") is provider_registery["email"]
    assert manager.get_provider(ProviderType.EMAIL) is provider_registery["email"]
    assert ProviderManager.get_provider("email") is provider_registery["email"]


@pytest.mark.parametrize(
    "provider_type",
    ["push", "sms", ProviderType.PUSH, ProviderType.SMS, "unknown"],
)
def test_provider_manager_raises_for_unregistered_provider(
    provider_type: str | ProviderType,
) -> None:
    with pytest.raises(ProviderNotFoundError):
        ProviderManager().get_provider(provider_type)


def test_email_provider_sends_rendered_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSendGridClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.messages = []

        def send(self, message):
            self.messages.append(message)
            return SimpleNamespace(status_code=202)

    clients: list[FakeSendGridClient] = []

    def client_factory(api_key: str) -> FakeSendGridClient:
        client = FakeSendGridClient(api_key)
        clients.append(client)
        return client

    monkeypatch.setattr(
        "app.services.notification.provider.email_provider.SendGridAPIClient",
        client_factory,
    )
    provider = EmailProvider(
        api_key="test-key",
        from_email="sender@example.com",
        from_name="CourseWave",
    )
    notification = make_notification(
        {
            "template_type": "email_verification",
            "content": {"link": "https://example.com/verify?token=abc"},
            "to_email": "student@example.com",
        }
    )

    provider.process(notification)

    assert len(clients) == 1
    assert clients[0].api_key == "test-key"
    assert len(clients[0].messages) == 1

    message = clients[0].messages[0].get()
    assert message["from"] == {
        "name": "CourseWave",
        "email": "sender@example.com",
    }
    assert message["subject"] == EMAIL_SUBJECT_REGISTRY["email_verification"]
    assert message["personalizations"] == [
        {"to": [{"email": "student@example.com"}]}
    ]
    assert message["content"][0]["type"] == "text/html"
    assert (
        '<a href="https://example.com/verify?token=abc">Verify email</a>'
        in message["content"][0]["value"]
    )


def test_email_provider_accepts_email_recipient_and_json_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = []

    class FakeSendGridClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def send(self, message):
            sent.append(message)
            return SimpleNamespace(status_code=202)

    monkeypatch.setattr(
        "app.services.notification.provider.email_provider.SendGridAPIClient",
        FakeSendGridClient,
    )
    provider = EmailProvider(api_key="test-key")
    notification = make_notification(
        {
            "template_type": "otp",
            "content": '{"otp": "123456"}',
            "email": "student@example.com",
        }
    )

    provider.process(notification)

    message = sent[0].get()
    assert message["subject"] == EMAIL_SUBJECT_REGISTRY["otp"]
    assert "<strong>123456</strong>" in message["content"][0]["value"]


def test_email_provider_wraps_sendgrid_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingSendGridClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def send(self, message) -> None:
            raise RuntimeError("SendGrid is unavailable")

    monkeypatch.setattr(
        "app.services.notification.provider.email_provider.SendGridAPIClient",
        FailingSendGridClient,
    )
    provider = EmailProvider(api_key="test-key")
    notification = make_notification(
        {
            "template_type": "email_verification",
            "content": {"link": "https://example.com/verify"},
            "to_email": "student@example.com",
        }
    )

    with pytest.raises(ProviderNotAvailableError) as error:
        provider.process(notification)

    assert isinstance(error.value.__cause__, RuntimeError)


def test_email_provider_rejects_non_success_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BadStatusClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def send(self, message):
            return SimpleNamespace(status_code=400)

    monkeypatch.setattr(
        "app.services.notification.provider.email_provider.SendGridAPIClient",
        BadStatusClient,
    )
    provider = EmailProvider(api_key="test-key")
    notification = make_notification(
        {
            "template_type": "email_verification",
            "content": {"link": "https://example.com/verify"},
            "to_email": "student@example.com",
        }
    )

    with pytest.raises(ProviderNotAvailableError, match="status 400"):
        provider.process(notification)


def test_email_provider_requires_a_recipient() -> None:
    provider = EmailProvider(api_key="test-key")
    notification = make_notification(
        {
            "template_type": "email_verification",
            "content": {"link": "https://example.com/verify"},
        }
    )

    with pytest.raises(ProviderNotAvailableError, match="recipient"):
        provider.process(notification)
