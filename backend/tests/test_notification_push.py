"""Tests for the SSE push-notification endpoint."""

import json
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from tests.fakes import FakeRedis
from tests.helpers import access_token, auth_headers, build_settings, seed_session

PUSH_PATH = "/notification/push"
USER = {
    "id": "notification-user-123",
    "email": "ada@example.com",
    "role": "student",
}


class FakePubSub:
    """Async pub/sub double that returns queued messages."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = list(messages)
        self.subscribed: str | None = None
        self.unsubscribed: str | None = None
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed = channel

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed = channel

    async def get_message(
        self,
        *,
        ignore_subscribe_messages: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any] | None:
        if self.messages:
            return self.messages.pop(0)
        return None

    async def aclose(self) -> None:
        self.closed = True


class FakeNotificationRedis(FakeRedis):
    """Fake Redis client with a pub/sub factory."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        super().__init__()
        self.pubsub_instance = FakePubSub(messages)

    def pubsub(self) -> FakePubSub:
        return self.pubsub_instance


def read_first_data_event(response: Any) -> str:
    """Read SSE lines until the first data event arrives."""

    for line in response.iter_lines():
        if line.startswith("data: "):
            return line
    raise AssertionError("No SSE data event received")


def test_push_notification_streams_only_authenticated_user_messages() -> None:
    settings = build_settings(
        auto_create_tables=False,
        cache_enabled=False,
        rate_limit_enabled=False,
    )
    redis = FakeNotificationRedis(
        [
            {
                "type": "message",
                "data": json.dumps(
                    {
                        "recipient_id": "another-user",
                        "message": {"title": "Not for Ada"},
                    }
                ),
            },
            {
                "type": "message",
                "data": json.dumps(
                    {
                        "recipient_id": USER["id"],
                        "message": {"title": "For Ada"},
                    }
                ),
            },
        ]
    )
    seed_session(redis, user=USER)
    app = create_app(settings, redis_client=redis)  # type: ignore[arg-type]

    with TestClient(app) as client:
        with client.stream(
            "GET",
            PUSH_PATH,
            headers=auth_headers(access_token()),
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(
                "text/event-stream"
            )
            event = read_first_data_event(response)

    payload = json.loads(event[len("data: "):])
    assert payload["recipient_id"] == USER["id"]
    assert payload["message"] == {"title": "For Ada"}
    assert redis.pubsub_instance.subscribed == settings.notification_topic
    assert redis.pubsub_instance.unsubscribed == settings.notification_topic
    assert redis.pubsub_instance.closed is True


def test_push_notification_skips_malformed_and_unidentified_messages() -> None:
    settings = build_settings(
        auto_create_tables=False,
        cache_enabled=False,
        rate_limit_enabled=False,
    )
    redis = FakeNotificationRedis(
        [
            {"type": "message", "data": "not-json"},
            {
                "type": "message",
                "data": json.dumps({"message": {"title": "No recipient"}}),
            },
            {
                "type": "message",
                "data": json.dumps(
                    {
                        "user_id": USER["id"],
                        "message": {"title": "For Ada"},
                    }
                ),
            },
        ]
    )
    seed_session(redis, user=USER)
    app = create_app(settings, redis_client=redis)  # type: ignore[arg-type]

    with TestClient(app) as client:
        with client.stream(
            "GET",
            PUSH_PATH,
            headers=auth_headers(access_token()),
        ) as response:
            event = read_first_data_event(response)

    payload = json.loads(event[len("data: "):])
    assert payload["user_id"] == USER["id"]
    assert payload["message"] == {"title": "For Ada"}


def test_push_notification_requires_authentication() -> None:
    settings = build_settings(
        auto_create_tables=False,
        cache_enabled=False,
        rate_limit_enabled=False,
    )
    redis = FakeNotificationRedis([])
    app = create_app(settings, redis_client=redis)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.get(PUSH_PATH)

    assert response.status_code == 401
