"""Tests for notification providers."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.services.notification.exception import (
    ProviderError,
    ProviderNotFoundError,
    ProviderServiceNotAvailbleError,
    ProviderServiceNotAvailableError,
)
from app.services.notification.model import (
    Notification,
    NotificationAudience,
    NotificationType,
)
from app.services.notification.provider import ProviderManager, ProviderType
from app.services.notification.provider.base import BaseProvider
from app.services.notification.provider.cache_mixin import (
    PROCESSED_NOTIFICATION_TTL_SECONDS,
    PROCESSED_NOTIFICATION_VALUE,
)
from app.services.notification.provider.email_provider import (
    EMAIL_SUBJECT_REGISTRY,
    EmailProvider,
)
from app.services.notification.provider.provider_registery import provider_registery
from app.services.notification.provider.push_provider import PushProvider
from app.services.notification.provider.sms_provider import SMSProvider


class FakeSyncRedis:
    """Minimal synchronous Redis double for notification cache tests."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.fail_exists = False
        self.fail_set = False

    def exists(self, key: str) -> int:
        if self.fail_exists:
            raise RedisError("Redis read failed")
        return int(key in self.values)

    def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
    ) -> bool:
        if self.fail_set:
            raise RedisError("Redis write failed")
        self.values[key] = value
        self.ttls[key] = ex
        return True


def make_notification(payload: dict) -> Notification:
    """Build an email notification with a predictable audience."""

    return Notification(
        type=NotificationType.EMAIL,
        payload=payload,
        belongs_to=NotificationAudience.STUDENT,
        user_id=uuid4(),
    )


def notification_cache_key(notification: Notification) -> str:
    """Return the expected processed-notification cache key."""

    return (
        f"notification:processed:{notification.type.value}:"
        f"{notification.notification_id}"
    )


def test_provider_registry_contains_supported_providers() -> None:
    assert isinstance(provider_registery["email"], EmailProvider)
    assert isinstance(provider_registery["push"], PushProvider)
    assert isinstance(provider_registery["sms"], SMSProvider)

    for provider in provider_registery.values():
        assert isinstance(provider, BaseProvider)


@pytest.mark.parametrize(
    ("provider_type", "provider_class"),
    [
        ("email", EmailProvider),
        ("push", PushProvider),
        ("sms", SMSProvider),
        (ProviderType.EMAIL, EmailProvider),
        (ProviderType.PUSH, PushProvider),
        (ProviderType.SMS, SMSProvider),
    ],
)
def test_provider_manager_returns_registered_provider(
    provider_type: str | ProviderType,
    provider_class: type[BaseProvider],
) -> None:
    provider = ProviderManager.get_provider(provider_type)
    provider_key = (
        provider_type.value
        if isinstance(provider_type, ProviderType)
        else provider_type
    )

    assert isinstance(provider, provider_class)
    assert provider is provider_registery[provider_key]


def test_provider_manager_accepts_notification_type() -> None:
    assert isinstance(
        ProviderManager.get_provider(NotificationType.EMAIL),
        EmailProvider,
    )
    assert isinstance(
        ProviderManager.get_provider(NotificationType.PUSH),
        PushProvider,
    )
    assert isinstance(
        ProviderManager.get_provider(NotificationType.SMS),
        SMSProvider,
    )


def test_provider_service_error_spelling_alias() -> None:
    assert ProviderServiceNotAvailbleError is ProviderServiceNotAvailableError


@pytest.mark.parametrize("provider_type", ["unknown", "email_v2"])
def test_provider_manager_raises_for_unregistered_provider(
    provider_type: str,
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
    redis = FakeSyncRedis()
    provider = EmailProvider(
        api_key="test-key",
        from_email="sender@example.com",
        from_name="CourseWave",
        redis_client=redis,
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

    cache_key = notification_cache_key(notification)
    assert redis.values[cache_key] == PROCESSED_NOTIFICATION_VALUE
    assert redis.ttls[cache_key] == PROCESSED_NOTIFICATION_TTL_SECONDS

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


def test_notification_cache_stores_processed_marker() -> None:
    redis = FakeSyncRedis()
    provider = EmailProvider(api_key="test-key", redis_client=redis)
    notification = make_notification({})
    cache_key = notification_cache_key(notification)

    assert provider.is_notification_cached(notification) is False

    provider.cache_notification(notification)

    assert provider.is_notification_cached(notification) is True
    assert redis.values[cache_key] == PROCESSED_NOTIFICATION_VALUE
    assert redis.ttls[cache_key] == PROCESSED_NOTIFICATION_TTL_SECONDS


def test_email_provider_skips_cached_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeSyncRedis()
    notification = make_notification({})
    redis.values[notification_cache_key(notification)] = (
        PROCESSED_NOTIFICATION_VALUE
    )

    class UnexpectedSendGridClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def send(self, message) -> None:
            pytest.fail("SendGrid must not be called for a cached notification")

    monkeypatch.setattr(
        "app.services.notification.provider.email_provider.SendGridAPIClient",
        UnexpectedSendGridClient,
    )
    provider = EmailProvider(api_key="test-key", redis_client=redis)

    provider.process(notification)


def test_notification_cache_fails_open() -> None:
    redis = FakeSyncRedis()
    redis.fail_exists = True
    redis.fail_set = True
    provider = EmailProvider(api_key="test-key", redis_client=redis)
    notification = make_notification({})

    assert provider.is_notification_cached(notification) is False
    provider.cache_notification(notification)


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
    provider = EmailProvider(
        api_key="test-key",
        redis_client=FakeSyncRedis(),
    )
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
    redis = FakeSyncRedis()
    provider = EmailProvider(
        api_key="test-key",
        redis_client=redis,
    )
    notification = make_notification(
        {
            "template_type": "email_verification",
            "content": {"link": "https://example.com/verify"},
            "to_email": "student@example.com",
        }
    )

    with pytest.raises(ProviderServiceNotAvailableError) as error:
        provider.process(notification)

    assert isinstance(error.value.__cause__, RuntimeError)
    assert redis.values == {}


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
    redis = FakeSyncRedis()
    provider = EmailProvider(
        api_key="test-key",
        redis_client=redis,
    )
    notification = make_notification(
        {
            "template_type": "email_verification",
            "content": {"link": "https://example.com/verify"},
            "to_email": "student@example.com",
        }
    )

    with pytest.raises(
        ProviderServiceNotAvailableError,
        match="status 400",
    ):
        provider.process(notification)

    assert redis.values == {}


def test_email_provider_rejects_invalid_sendgrid_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InvalidResponseClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def send(self, message):
            return SimpleNamespace()

    monkeypatch.setattr(
        "app.services.notification.provider.email_provider.SendGridAPIClient",
        InvalidResponseClient,
    )
    provider = EmailProvider(
        api_key="test-key",
        redis_client=FakeSyncRedis(),
    )
    notification = make_notification(
        {
            "template_type": "email_verification",
            "content": {"link": "https://example.com/verify"},
            "to_email": "student@example.com",
        }
    )

    with pytest.raises(ProviderServiceNotAvailableError, match="invalid response"):
        provider.process(notification)


def test_email_provider_wraps_processing_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_to_render(*args, **kwargs) -> None:
        raise RuntimeError("template rendering failed")

    monkeypatch.setattr(
        "app.services.notification.provider.email_provider.TemplateManager.get_template",
        fail_to_render,
    )
    provider = EmailProvider(
        api_key="test-key",
        redis_client=FakeSyncRedis(),
    )
    notification = make_notification(
        {
            "template_type": "email_verification",
            "content": {"link": "https://example.com/verify"},
            "to_email": "student@example.com",
        }
    )

    with pytest.raises(ProviderError) as error:
        provider.process(notification)

    assert isinstance(error.value.__cause__, RuntimeError)


def test_email_provider_requires_a_recipient() -> None:
    provider = EmailProvider(
        api_key="test-key",
        redis_client=FakeSyncRedis(),
    )
    notification = make_notification(
        {
            "template_type": "email_verification",
            "content": {"link": "https://example.com/verify"},
        }
    )

    with pytest.raises(ProviderError, match="recipient"):
        provider.process(notification)


def test_forgot_password_email_subject_is_registered() -> None:
    assert EMAIL_SUBJECT_REGISTRY["forgot_password"] == "reset your password"
