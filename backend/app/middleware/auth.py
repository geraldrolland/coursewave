"""BaseHTTPMiddleware that validates access tokens and loads Redis sessions."""

import json
import logging
from collections.abc import Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.base_config import Settings, split_csv
from app.security import decode_token

logger = logging.getLogger("backend.auth")

MISSING_AUTHORIZATION = "Missing or invalid Authorization header"
INVALID_TOKEN = "Invalid or expired token"
INVALID_TOKEN_TYPE = "Invalid token type"
INVALID_TOKEN_PAYLOAD = "Invalid token payload"
SESSION_NOT_FOUND = "Session expired or not found"
AUTH_UNAVAILABLE = "Authentication service unavailable"


class AuthMiddleware(BaseHTTPMiddleware):
    """Require a Bearer access token backed by a Redis session."""

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        redis_provider: Callable[[], Redis],
    ) -> None:
        super().__init__(app)
        self.settings = settings
        self.redis_provider = redis_provider
        self.exclude_paths = {
            path.rstrip("/") or "/"
            for path in split_csv(settings.auth_exclude_paths)
        }

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        path = request.url.path.rstrip("/") or "/"
        if path in self.exclude_paths:
            return await call_next(request)

        authorization = request.headers.get("authorization")
        if not authorization or not authorization.lower().startswith("bearer "):
            return self._reject(401, MISSING_AUTHORIZATION)

        token = authorization[7:].strip()
        if not token:
            return self._reject(401, MISSING_AUTHORIZATION)

        try:
            payload = decode_token(token, self.settings)
        except Exception:
            return self._reject(401, INVALID_TOKEN)

        if payload.get("type") != "access":
            return self._reject(401, INVALID_TOKEN_TYPE)

        session_id = payload.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            return self._reject(401, INVALID_TOKEN_PAYLOAD)

        try:
            raw_session = await self.redis_provider().get(f"session:{session_id}")
        except RedisError:
            logger.exception("Redis failed while loading an authentication session")
            return self._reject(500, AUTH_UNAVAILABLE)

        if raw_session is None:
            return self._reject(401, SESSION_NOT_FOUND)

        try:
            if isinstance(raw_session, bytes):
                raw_session = raw_session.decode("utf-8")
            user = json.loads(raw_session)
        except (TypeError, ValueError):
            logger.exception("Stored authentication session is not valid JSON")
            return self._reject(500, AUTH_UNAVAILABLE)

        if not isinstance(user, dict):
            logger.error("Stored authentication session is not a JSON object")
            return self._reject(500, AUTH_UNAVAILABLE)

        request.state.user = user
        return await call_next(request)

    @staticmethod
    def _reject(status_code: int, detail: str) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={"detail": detail},
        )
