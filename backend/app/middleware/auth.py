"""ASGI middleware that validates access tokens and loads Redis sessions."""

import json
import logging
from collections.abc import Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.base_config import Settings, split_csv
from app.security import decode_token

logger = logging.getLogger("backend.auth")

MISSING_AUTHORIZATION = "Missing or invalid Authorization header"
INVALID_TOKEN = "Invalid or expired token"
INVALID_TOKEN_TYPE = "Invalid token type"
INVALID_TOKEN_PAYLOAD = "Invalid token payload"
SESSION_NOT_FOUND = "Session expired or not found"
AUTH_UNAVAILABLE = "Authentication service unavailable"


class AuthMiddleware:
    """Require a Bearer access token backed by a Redis session."""

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        redis_provider: Callable[[], Redis],
    ) -> None:
        self.app = app
        self.settings = settings
        self.redis_provider = redis_provider
        self.exclude_paths = {
            path.rstrip("/") or "/"
            for path in split_csv(settings.auth_exclude_paths)
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = (scope.get("path") or "").rstrip("/") or "/"
        if path in self.exclude_paths:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        authorization = headers.get("authorization")
        if not authorization or not authorization.lower().startswith("bearer "):
            await self._reject(scope, receive, send, 401, MISSING_AUTHORIZATION)
            return

        token = authorization[7:].strip()
        if not token:
            await self._reject(scope, receive, send, 401, MISSING_AUTHORIZATION)
            return

        try:
            payload = decode_token(token, self.settings)
        except Exception:
            await self._reject(scope, receive, send, 401, INVALID_TOKEN)
            return

        if payload.get("type") != "access":
            await self._reject(scope, receive, send, 401, INVALID_TOKEN_TYPE)
            return

        session_id = payload.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            await self._reject(scope, receive, send, 401, INVALID_TOKEN_PAYLOAD)
            return

        try:
            raw_session = await self.redis_provider().get(f"session:{session_id}")
        except RedisError:
            logger.exception("Redis failed while loading an authentication session")
            await self._reject(scope, receive, send, 500, AUTH_UNAVAILABLE)
            return

        if raw_session is None:
            await self._reject(scope, receive, send, 401, SESSION_NOT_FOUND)
            return

        try:
            if isinstance(raw_session, bytes):
                raw_session = raw_session.decode("utf-8")
            user = json.loads(raw_session)
        except (TypeError, ValueError):
            logger.exception("Stored authentication session is not valid JSON")
            await self._reject(scope, receive, send, 500, AUTH_UNAVAILABLE)
            return

        if not isinstance(user, dict):
            logger.error("Stored authentication session is not a JSON object")
            await self._reject(scope, receive, send, 500, AUTH_UNAVAILABLE)
            return

        state = scope.setdefault("state", {})
        state["user"] = user

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        detail: str,
    ) -> None:
        response = JSONResponse(
            status_code=status_code,
            content={"detail": detail},
        )
        await response(scope, receive, send)
