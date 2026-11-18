"""Server-sent notification streaming routes."""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

logger = logging.getLogger("notification.router")

SSE_POLL_TIMEOUT_SECONDS = 1.0
SSE_KEEPALIVE = ": keepalive\n\n"
INVALID_AUTH_SESSION = "Invalid authentication session"

router = APIRouter(prefix="/notification", tags=["notifications"])


def _authenticated_user_id(request: Request) -> str:
    """Return the authenticated session user ID."""

    user = getattr(request.state, "user", None)
    if not isinstance(user, dict):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_AUTH_SESSION,
        )

    user_id = user.get("id")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_AUTH_SESSION,
        )

    return user_id


def _decode_payload(raw_data: Any) -> dict[str, Any] | None:
    """Decode a Redis message into a JSON object."""

    try:
        if isinstance(raw_data, bytes):
            raw_data = raw_data.decode("utf-8")
        payload = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
    except (TypeError, ValueError):
        logger.warning("Notification channel received non-JSON data")
        return None

    if not isinstance(payload, dict):
        logger.warning("Notification channel received a non-object payload")
        return None

    return payload


def _payload_recipient_id(payload: dict[str, Any]) -> str | None:
    """Return the recipient ID from a push payload."""

    recipient_id = payload.get("recipient_id")
    if recipient_id is None:
        recipient_id = payload.get("user_id")
    if recipient_id is None:
        return None
    return str(recipient_id)


async def _stream_notifications(
    pubsub: Any,
    channel: str,
    user_id: str,
    request: Request,
) -> AsyncIterator[str]:
    """Subscribe to Redis and yield only the authenticated user's messages."""

    try:
        await pubsub.subscribe(channel)
        while not await request.is_disconnected():
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=SSE_POLL_TIMEOUT_SECONDS,
            )
            if message is None:
                yield SSE_KEEPALIVE
                continue

            payload = _decode_payload(message.get("data"))
            if payload is None:
                continue

            if _payload_recipient_id(payload) != user_id:
                continue

            yield f"data: {json.dumps(payload)}\n\n"
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:
            logger.warning(
                "Failed to unsubscribe from notification channel",
                exc_info=True,
            )
        try:
            await pubsub.aclose()
        except Exception:
            logger.warning(
                "Failed to close notification subscription",
                exc_info=True,
            )


@router.get(
    "/push",
    response_class=StreamingResponse,
    status_code=status.HTTP_200_OK,
)
async def stream_push_notifications(request: Request) -> StreamingResponse:
    """Stream push notifications for the authenticated user over SSE."""

    user_id = _authenticated_user_id(request)
    settings = request.app.state.settings
    redis: Redis = request.app.state.redis

    pubsub = redis.pubsub()
    return StreamingResponse(
        _stream_notifications(
            pubsub,
            settings.notification_topic,
            user_id,
            request,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]
