"""WebSocket notification endpoints."""

import asyncio
import logging

from fastapi import APIRouter, Depends
from starlette.websockets import WebSocket

from app.services.notification.ws.authentication import authenticate_user
from app.services.notification.ws.websocket_notif_manager import (
    websocket_notification_manager,
)

logger = logging.getLogger("notification.ws")

WEBSOCKET_SLEEP_SECONDS = 30

router = APIRouter()


@router.websocket("/notification")
async def notification_websocket(
    websocket: WebSocket,
    user: dict = Depends(authenticate_user),
) -> None:
    """Authenticate and keep a notification WebSocket open."""

    await websocket.accept()
    user_id = str(user["id"])
    websocket_notification_manager.register(user_id, websocket)
    logger.info("WebSocket connected for user %s", user_id)

    try:
        while True:
            await asyncio.sleep(WEBSOCKET_SLEEP_SECONDS)
    finally:
        websocket_notification_manager.remove(user_id, websocket)
        logger.info("WebSocket disconnected for user %s", user_id)


__all__ = ["router"]
