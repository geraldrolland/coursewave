"""BaseHTTPMiddleware that logs a structured record for every HTTP request."""

import logging
import time
import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

REQUEST_COMPLETED = "Request completed"
REQUEST_FAILED = "Request failed"


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    """Log method, path, status code, message, and request ID per request."""

    def __init__(
        self,
        app: ASGIApp,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(app)
        self.logger = logger or logging.getLogger("backend.request")

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id

        status_code = 500
        start = time.perf_counter()

        try:
            response = await call_next(request)
            status_code = response.status_code
            if "x-request-id" not in response.headers:
                response.headers["X-REQUEST-ID"] = request_id
            return response
        finally:
            message = (
                REQUEST_COMPLETED if status_code < 400 else REQUEST_FAILED
            )

            fields: dict[str, Any] = {
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "message": message,
                "request_id": request_id,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            }
            self.logger.info(message, extra={"fields": fields})
