"""Redis-backed rate limiting middleware."""

import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


@dataclass(frozen=True)
class RateLimitRoute:
    """A prefix, window, and strategy selected from ``RATELIMIT_ROUTES``."""

    prefix: str
    window_ms: int
    strategy: str


def _first(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _normalize_strategy(value: Any) -> str:
    normalized = re.sub(r"[\s_-]", "", str(value)).lower()
    aliases = {
        "fixed": "fixed_window",
        "fixedwindow": "fixed_window",
        "bucket": "token_bucket",
        "tokenbucket": "token_bucket",
    }
    return aliases.get(normalized, normalized)


def _normalize_prefix(value: str) -> str:
    prefix = value.strip()
    if prefix == WILDCARD_PREFIX:
        return WILDCARD_PREFIX

    prefix = f"/{prefix.lstrip('/')}".rstrip("/")
    if not prefix:
        return WILDCARD_PREFIX
    return prefix


def parse_ratelimit_routes(entries: object) -> list[RateLimitRoute]:
    """Turn the list-of-dicts setting into validated runtime routes.

    Malformed entries are ignored with a warning. Unknown strategies fall back
    to ``fixed_window`` so a typo cannot silently disable rate limiting.
    """

    if entries is None:
        return []

    if not isinstance(entries, list):
        logger.warning("Ignoring RATELIMIT_ROUTES because it is not a list")
        return []

    routes: list[RateLimitRoute] = []
    for entry in entries:
        if not isinstance(entry, dict):
            logger.warning("Ignoring malformed RATELIMIT_ROUTES entry: %r", entry)
            continue

        raw_prefix = entry.get("prefix")
        if not isinstance(raw_prefix, str) or not raw_prefix.strip():
            logger.warning("Ignoring RATELIMIT_ROUTES entry without a prefix: %r", entry)
            continue

        raw_window_ms = _first(entry, "window_ms", "windowMs", default=DEFAULT_WINDOW_MS)
        try:
            window_ms = max(1, int(raw_window_ms))
        except (TypeError, ValueError):
            logger.warning("Ignoring malformed RATELIMIT_ROUTES entry: %r", entry)
            continue

        raw_strategy = entry.get("strategy", DEFAULT_STRATEGY)
        strategy = _normalize_strategy(raw_strategy)
        if strategy not in RATELIMITER_REGISTRY:
            logger.warning(
                "Unknown rate-limit strategy %r; using %s",
                raw_strategy,
                DEFAULT_STRATEGY,
            )
            strategy = DEFAULT_STRATEGY

        routes.append(
            RateLimitRoute(
                prefix=_normalize_prefix(raw_prefix),
                window_ms=window_ms,
                strategy=strategy,
            )
        )

    # Longest non-wildcard prefix wins; the wildcard is always checked last.
    routes.sort(
        key=lambda route: (
            route.prefix == WILDCARD_PREFIX,
            -len(route.prefix),
        )
    )
    return routes


class RateLimitMiddleware:
    """Apply the configured strategy for each request's matching prefix."""

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        redis_provider: Callable[[], Redis],
    ) -> None:
        self.app = app
        self.redis_provider = redis_provider
        self.enabled = settings.rate_limit_enabled
        self.max_requests = max(1, settings.rate_limit_max)
        self.exclude_paths = {
            path.rstrip("/") or "/"
            for path in split_csv(settings.rate_limit_exclude_paths)
        }
        self.routes = parse_ratelimit_routes(settings.ratelimit_routes)
        self.fallback_route = RateLimitRoute(
            prefix=WILDCARD_PREFIX,
            window_ms=DEFAULT_WINDOW_MS,
            strategy=DEFAULT_STRATEGY,
        )
        self.handlers = {
            strategy: partial(
                handler,
                redis_provider=self.redis_provider,
                max_requests=self.max_requests,
            )
            for strategy, handler in RATELIMITER_REGISTRY.items()
        }

    def _match(self, path: str) -> RateLimitRoute:
        for route in self.routes:
            if route.prefix == WILDCARD_PREFIX:
                return route
            if path == route.prefix or path.startswith(f"{route.prefix}/"):
                return route

        return self.fallback_route

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        path = (scope.get("path") or "").rstrip("/") or "/"
        if path in self.exclude_paths:
            await self.app(scope, receive, send)
            return

        route = self._match(path)
        identifier = client_identifier(scope)
        key = f"ratelimit:{route.strategy}:{route.prefix}:{identifier}"

        state = scope.setdefault("state", {})
        state["ratelimit"] = {
            "key": key,
            "prefix": route.prefix,
            "strategy": route.strategy,
            "window_ms": route.window_ms,
        }
        state.pop("ratelimit_result", None)

        handler = self.handlers.get(route.strategy)
        if handler is None:
            logger.warning(
                "Rate-limit strategy %r is not registered; using %s",
                route.strategy,
                DEFAULT_STRATEGY,
            )
            handler = self.handlers[DEFAULT_STRATEGY]

        try:
            allowed = bool(await handler(scope, route.window_ms))
        except RedisError:
            logger.warning("Redis rate limiter failed; allowing the request")
            await self.app(scope, receive, send)
            return

        result = state.get("ratelimit_result")
        if not isinstance(result, RateLimitResult):
            fallback_seconds = max(1, math.ceil(route.window_ms / 1000))
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
            response = JSONResponse(
                status_code=429,
                content={"detail": TOO_MANY_REQUESTS},
                headers=headers,
            )
            await response(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for name, value in headers.items():
                    response_headers[name] = value
            await send(message)

        await self.app(scope, receive, send_wrapper)
