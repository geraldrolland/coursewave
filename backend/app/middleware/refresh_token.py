"""ASGI middleware that implements the refresh-token endpoint."""

import logging
from collections.abc import Callable
from http.cookies import CookieError, SimpleCookie
from urllib.parse import quote

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.base_config import Settings
from app.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    sign_cookie_value,
    unsign_cookie_value,
)

logger = logging.getLogger("backend.refresh")

REFRESH_PATH = "/auth/api/v1/token-refresh"
REFRESH_COOKIE_NAME = "refresh"

MISSING_REFRESH_TOKEN = "Missing refresh token"
INVALID_REFRESH_TOKEN = "Invalid or expired refresh token"
INVALID_TOKEN_TYPE = "Invalid token type"
INVALID_REFRESH_PAYLOAD = "Invalid refresh token payload"
SESSION_NOT_FOUND = "Session expired or not found"
REFRESH_FAILED = "Token refresh failed"


class RefreshTokenMiddleware:
    """Handle ``POST /auth/api/v1/token-refresh`` and pass everything else on."""

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        redis_provider: Callable[[], Redis],
    ) -> None:
        self.app = app
        self.settings = settings
        self.redis_provider = redis_provider

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = (scope.get("path") or "").rstrip("/") or "/"
        if scope.get("method") != "POST" or path != REFRESH_PATH:
            await self.app(scope, receive, send)
            return

        await self._handle_refresh(scope, receive, send)

    async def _handle_refresh(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        raw_cookie = Headers(scope=scope).get("cookie")
        signed_value = self._cookie_value(raw_cookie, REFRESH_COOKIE_NAME)
        if signed_value is None:
            await self._reject(scope, receive, send, 401, MISSING_REFRESH_TOKEN)
            return

        token = unsign_cookie_value(signed_value, self.settings.cookie_secret)
        if token is None:
            await self._reject(scope, receive, send, 401, MISSING_REFRESH_TOKEN)
            return

        try:
            payload = decode_token(token, self.settings)
        except Exception:
            await self._reject(scope, receive, send, 401, INVALID_REFRESH_TOKEN)
            return

        if payload.get("type") != "refresh":
            await self._reject(scope, receive, send, 401, INVALID_TOKEN_TYPE)
            return

        session_id = payload.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            await self._reject(scope, receive, send, 401, INVALID_REFRESH_PAYLOAD)
            return

        session_key = f"session:{session_id}"
        redis = self.redis_provider()

        try:
            session = await redis.get(session_key)
        except RedisError:
            logger.exception("Redis failed while loading a refresh session")
            await self._reject(scope, receive, send, 500, REFRESH_FAILED)
            return

        if session is None:
            await self._reject(scope, receive, send, 401, SESSION_NOT_FOUND)
            return

        try:
            await redis.expire(
                session_key,
                self.settings.refresh_token_max_age_seconds,
            )
            access_token = create_access_token(session_id, self.settings)
            refresh_token = create_refresh_token(session_id, self.settings)
        except RedisError:
            logger.exception("Redis failed while extending a refresh session")
            await self._reject(scope, receive, send, 500, REFRESH_FAILED)
            return
        except Exception:
            logger.exception("Unexpected failure while refreshing a token")
            await self._reject(scope, receive, send, 500, REFRESH_FAILED)
            return

        cookie_value = quote(
            sign_cookie_value(refresh_token, self.settings.cookie_secret),
            safe="",
        )
        max_age = self.settings.refresh_token_max_age_seconds

        response = JSONResponse(content={"accessToken": access_token})
        response.headers.append(
            "set-cookie",
            (
                f"{REFRESH_COOKIE_NAME}={cookie_value}; "
                "HttpOnly; Secure; SameSite=Strict; "
                f"Path={REFRESH_PATH}; Max-Age={max_age}"
            ),
        )
        await response(scope, receive, send)

    @staticmethod
    def _cookie_value(raw_cookie: str | None, name: str) -> str | None:
        if not raw_cookie:
            return None

        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
        except CookieError:
            return None

        morsel = cookie.get(name)
        return morsel.value if morsel is not None else None

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
