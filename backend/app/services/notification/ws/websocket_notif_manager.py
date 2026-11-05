"""WebSocket connection registry for notification delivery."""

import logging

from starlette.websockets import WebSocket, WebSocketState

logger = logging.getLogger("notification.ws.manager")


class WebsocketNotificationManager:
    """Track active WebSocket connections by user ID."""

    def __init__(self) -> None:
        self.connections: dict[str, list[WebSocket]] = {}

    def register(self, id: str, websocket: WebSocket) -> None:
        """Register a WebSocket for a user."""

        if id in self.connections:
            self.connections[id].append(websocket)
        else:
            self.connections[id] = [websocket]

    def remove(self, id: str, websocket: WebSocket) -> None:
        """Remove a WebSocket from a user's connection list."""

        connections = self.connections.get(id)
        if connections is None:
            return

        if websocket in connections:
            connections.remove(websocket)

        if not connections:
            self.connections.pop(id, None)

    async def send(self, id: str, message: dict) -> None:
        """Send a message to all active sockets for a user."""

        for websocket in list(self.connections.get(id, [])):
            if websocket.client_state != WebSocketState.CONNECTED:
                continue

            try:
                await websocket.send_json(message)
            except Exception:
                logger.warning(
                    "Failed to send WebSocket message to user %s",
                    id,
                    exc_info=True,
                )
                self.remove(id, websocket)


websocket_notification_manager = WebsocketNotificationManager()

__all__ = [
    "WebsocketNotificationManager",
    "websocket_notification_manager",
]
