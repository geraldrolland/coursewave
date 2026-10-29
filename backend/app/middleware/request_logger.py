"""ASGI middleware that logs a structured record for every HTTP request."""

import logging
import time
import uuid
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_COMPLETED = "Request completed"
REQUEST_FAILED = "Request failed"


class RequestLoggerMiddleware:
    """Log method, path, status code, message, and request ID per request."""

    def __init__(
        self,
        app: ASGIApp,
        logger: logging.Logger | None = None,
    ) -> None:
        self.app = app
        self.logger = logger or logging.getLogger("backend.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = headers.get("x-request-id") or str(uuid.uuid4())

        state = scope.setdefault("state", {})
        state["request_id"] = request_id

        status_code = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code

            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = MutableHeaders(scope=message)
                if "x-request-id" not in response_headers:
                    response_headers["X-REQUEST-ID"] = request_id

            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            message = (
                REQUEST_COMPLETED if status_code < 400 else REQUEST_FAILED
            )

            fields: dict[str, Any] = {
                "method": scope.get("method"),
                "path": scope.get("path"),
                "status_code": status_code,
                "message": message,
                "request_id": request_id,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            }
            self.logger.info(message, extra={"fields": fields})
