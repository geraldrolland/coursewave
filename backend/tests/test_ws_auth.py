"""Tests for WebSocket authentication and the notification endpoint."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.base_config import Settings
from app.services.notification.ws.authentication import authenticate_user
from app.services.notification.ws.websocket_notif_manager import (
    websocket_notification_manager,
)
from tests.fakes import FakeRedis
from tests.helpers import (
    TEST_USER,
    access_token,
    refresh_token,
    seed_session,
)


@pytest.fixture(autouse=True)
def clear_websocket_connections() -> None:
    websocket_notification_manager.connections.clear()
    yield
    websocket_notification_manager.connections.clear()


def test_valid_token_opens_notification_websocket(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    seed_session(fake_redis)
    token = access_token()

    with client.websocket_connect(f"/notification?token={token}"):
        pass


def test_missing_token_closes_with_policy_violation(
    client: TestClient,
) -> None:
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/notification"):
            pass

    assert error.value.code == 1008


def test_invalid_token_closes_with_policy_violation(
    client: TestClient,
) -> None:
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/notification?token=not-a-jwt"):
            pass

    assert error.value.code == 1008


def test_refresh_token_closes_with_policy_violation(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    seed_session(fake_redis)
    token = refresh_token()

    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(f"/notification?token={token}"):
            pass

    assert error.value.code == 1008


def test_missing_session_closes_with_policy_violation(
    client: TestClient,
) -> None:
    token = access_token()

    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(f"/notification?token={token}"):
            pass

    assert error.value.code == 1008


def test_redis_failure_closes_with_internal_error(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    fake_redis.fail("get")
    token = access_token()

    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(f"/notification?token={token}"):
            pass

    assert error.value.code == 1011


def test_invalid_stored_session_closes_with_internal_error(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    fake_redis.seed("session:session-123", "{not-json")
    token = access_token()

    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(f"/notification?token={token}"):
            pass

    assert error.value.code == 1011


def test_valid_websocket_is_registered(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    seed_session(fake_redis)
    token = access_token()

    with client.websocket_connect(f"/notification?token={token}"):
        assert websocket_notification_manager.connections


def test_authenticate_user_sets_session_in_scope(
    settings: Settings,
    fake_redis: FakeRedis,
) -> None:
    seed_session(fake_redis)
    websocket = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(settings=settings, redis=fake_redis)
        ),
        query_params={"token": access_token()},
        state=SimpleNamespace(),
    )

    user = asyncio.run(
        authenticate_user(websocket)  # type: ignore[arg-type]
    )

    assert user == TEST_USER
    assert websocket.state.user == TEST_USER
