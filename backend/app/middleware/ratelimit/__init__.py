"""Redis-backed rate limiting middleware."""

import logging
import math
from collections.abc import Callable
from functools import partial
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.base_config import Settings, split_csv
from app.middleware.ratelimit.ratelimiter_registry import RATELIMITER_REGISTRY
from app.middleware.ratelimit.utils import (
    RateLimitResult,
    client_identifier,
    rate_limit_headers,
)

logger = logging.getLogger("backend.ratelimit")

TOO_MANY_REQUESTS = "Too many requests, please try again later"
DEFAULT_WINDOW_MS = 90000
DEFAULT_STRATEGY = "fixed_window"
WILDCARD_PREFIX = "*"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply the handler for the first configured route prefix that matches."""

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        redis_provider: Callable[[], Redis],
    ) -> None:
        super().__init__(app)
        self.redis_provider = redis_provider
        self.enabled = settings.rate_limit_enabled
        self.max_requests = max(1, settings.rate_limit_max)
        self.exclude_paths = {
            path.rstrip("/") or "/"
            for path in split_csv(settings.rate_limit_exclude_paths)
        }
        self.routes = settings.ratelimit_routes
        self.handlers = {
            strategy: partial(
                handler,
                redis_provider=self.redis_provider,
                max_requests=self.max_requests,
            )
            for strategy, handler in RATELIMITER_REGISTRY.items()
        }

    def _match(self, path: str) -> dict[str, Any] | None:
        """Return the first configured route whose prefix matches ``path``."""

        for route in self.routes:
            if not isinstance(route, dict):
                continue

            prefix = str(route.get("prefix", "")).strip()
            if not prefix:
                continue

            if prefix == WILDCARD_PREFIX or path.startswith(prefix):
                return route

        return None

    @staticmethod
    def _route_config(route: dict[str, Any] | None) -> tuple[str, int, str]:
        """Resolve the prefix, window, and strategy for a matched route."""

        if route is None:
            return WILDCARD_PREFIX, DEFAULT_WINDOW_MS, DEFAULT_STRATEGY

        prefix = str(route.get("prefix", WILDCARD_PREFIX)).strip() or WILDCARD_PREFIX
        try:
            window_ms = max(1, int(route.get("window_ms", DEFAULT_WINDOW_MS)))
        except (TypeError, ValueError):
            window_ms = DEFAULT_WINDOW_MS

        return prefix, window_ms, str(route.get("strategy", DEFAULT_STRATEGY))

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if not self.enabled:
            return await call_next(request)

        path = request.url.path.rstrip("/") or "/"
        if path in self.exclude_paths:
            return await call_next(request)

        prefix, window_ms, strategy = self._route_config(self._match(path))

        handler = self.handlers.get(strategy)
        if handler is None:
            logger.warning(
                "Rate-limit strategy %r is not registered; using %s",
                strategy,
                DEFAULT_STRATEGY,
            )
            strategy = DEFAULT_STRATEGY
            handler = self.handlers[DEFAULT_STRATEGY]

        identifier = client_identifier(request.scope)
        key = f"ratelimit:{strategy}:{prefix}:{identifier}"

        state = request.scope.setdefault("state", {})
        state["ratelimit"] = {
            "key": key,
            "prefix": prefix,
            "strategy": strategy,
            "window_ms": window_ms,
        }
        state.pop("ratelimit_result", None)

        try:
            allowed = bool(await handler(request.scope, window_ms))
        except RedisError:
            logger.warning("Redis rate limiter failed; allowing the request")
            return await call_next(request)

        result = state.get("ratelimit_result")
        if not isinstance(result, RateLimitResult):
            fallback_seconds = max(1, math.ceil(window_ms / 1000))
            result = RateLimitResult(
                limit=self.max_requests,
                remaining=0,
                reset_seconds=fallback_seconds,
                allowed=allowed,
                retry_after_seconds=0 if allowed else fallback_seconds,
            )
            state["ratelimit_result"] = result

        headers = rate_limit_headers(result)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": TOO_MANY_REQUESTS},
                headers=headers,
            )

        response = await call_next(request)
        for name, value in headers.items():
            response.headers[name] = value

        return response
