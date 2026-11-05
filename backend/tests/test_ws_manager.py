"""Tests for the WebSocket notification connection manager."""

import asyncio
from typing import Any

from starlette.websockets import WebSocketState

from app.services.notification.ws.websocket_notif_manager import (
    WebsocketNotificationManager,
)


class FakeWebSocket:
    """Minimal WebSocket double for manager tests."""

    def __init__(
        self,
        state: WebSocketState = WebSocketState.CONNECTED,
        fail: bool = False,
    ) -> None:
        self.client_state = state
        self.fail = fail
        self.messages: list[dict[str, Any]] = []

    async def send_json(self, message: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("WebSocket is unavailable")
        self.messages.append(message)


def test_register_and_remove_websocket() -> None:
    manager = WebsocketNotificationManager()
    first = FakeWebSocket()
    second = FakeWebSocket()

    manager.register("user-1", first)  # type: ignore[arg-type]
    manager.register("user-1", second)  # type: ignore[arg-type]

    assert manager.connections["user-1"] == [first, second]

    manager.remove("user-1", first)  # type: ignore[arg-type]
    assert manager.connections["user-1"] == [second]

    manager.remove("user-1", second)  # type: ignore[arg-type]
    assert "user-1" not in manager.connections


def test_send_only_targets_active_websockets() -> None:
    manager = WebsocketNotificationManager()
    active = FakeWebSocket()
    inactive = FakeWebSocket(state=WebSocketState.DISCONNECTED)
    manager.register("user-1", active)  # type: ignore[arg-type]
    manager.register("user-1", inactive)  # type: ignore[arg-type]

    asyncio.run(manager.send("user-1", {"message": "hello"}))

    assert active.messages == [{"message": "hello"}]
    assert inactive.messages == []


def test_send_removes_failed_websocket() -> None:
    manager = WebsocketNotificationManager()
    failing = FakeWebSocket(fail=True)
    healthy = FakeWebSocket()
    manager.register("user-1", failing)  # type: ignore[arg-type]
    manager.register("user-1", healthy)  # type: ignore[arg-type]

    asyncio.run(manager.send("user-1", {"message": "hello"}))

    assert manager.connections["user-1"] == [healthy]
    assert healthy.messages == [{"message": "hello"}]


def test_send_ignores_unknown_user() -> None:
    manager = WebsocketNotificationManager()

    asyncio.run(manager.send("missing", {"message": "hello"}))

    assert manager.connections == {}
