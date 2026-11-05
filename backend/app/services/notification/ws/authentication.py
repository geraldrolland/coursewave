"""WebSocket authentication dependency."""

import json
import logging
from typing import Any

from redis.exceptions import RedisError
from starlette import status
from starlette.exceptions import WebSocketException
from starlette.websockets import WebSocket

from app.security import decode_token

logger = logging.getLogger("notification.ws.auth")


async def authenticate_user(websocket: WebSocket) -> dict[str, Any]:
    """Authenticate a WebSocket using its access-token query parameter."""

    token = websocket.query_params.get("token")
    if not token:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Missing access token",
        )

    try:
        payload = decode_token(token, websocket.app.state.settings)
    except Exception as exc:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid or expired access token",
        ) from exc

    if payload.get("type") != "access":
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid token type",
        )

    session_id = payload.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid token payload",
        )

    try:
        raw_session = await websocket.app.state.redis.get(
            f"session:{session_id}"
        )
    except RedisError as exc:
        logger.exception("Redis failed while loading a WebSocket session")
        raise WebSocketException(
            code=status.WS_1011_INTERNAL_ERROR,
            reason="Authentication service unavailable",
        ) from exc

    if raw_session is None:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Session expired or not found",
        )

    try:
        if isinstance(raw_session, bytes):
            raw_session = raw_session.decode("utf-8")
        session = json.loads(raw_session)
    except (TypeError, ValueError) as exc:
        logger.exception("Stored WebSocket session is not valid JSON")
        raise WebSocketException(
            code=status.WS_1011_INTERNAL_ERROR,
            reason="Authentication service unavailable",
        ) from exc

    if not isinstance(session, dict):
        logger.error("Stored WebSocket session is not a JSON object")
        raise WebSocketException(
            code=status.WS_1011_INTERNAL_ERROR,
            reason="Authentication service unavailable",
        )

    user_id = session.get("userId")
    if not isinstance(user_id, str) or not user_id:
        logger.error("Stored WebSocket session has no userId")
        raise WebSocketException(
            code=status.WS_1011_INTERNAL_ERROR,
            reason="Authentication service unavailable",
        )

    websocket.state.user = session
    return session


__all__ = ["authenticate_user"]
