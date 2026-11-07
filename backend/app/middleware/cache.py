"""BaseHTTPMiddleware that caches public GET responses in Redis."""

import logging
from collections.abc import Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Scope

from app.base_config import Settings, split_csv

logger = logging.getLogger("backend.cache")

CACHE_KEY_PREFIX = "cache:"
MAX_CACHE_BODY_BYTES = 1024 * 1024
CACHE_ROUTES = (
    "/student/api/v1",
    "/instructor/api/v1",
)


def _is_json_content_type(value: str | None) -> bool:
    """Return whether ``value`` is ``application/json``, ignoring parameters."""

    if not value:
        return False
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type == "application/json"


def _matches_cache_route(path: str) -> bool:
    """Return whether ``path`` is under one of the cache route prefixes."""

    for route in CACHE_ROUTES:
        prefix = route.rstrip("/") or "/"
        if path == prefix or path.startswith(f"{prefix}/"):
            return True
    return False


class CacheMiddleware(BaseHTTPMiddleware):
    """Cache only unauthenticated, cookie-free GET responses."""

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        redis_provider: Callable[[], Redis],
    ) -> None:
        super().__init__(app)
        self.redis_provider = redis_provider
        self.enabled = settings.cache_enabled
        self.ttl_seconds = settings.cache_ttl_seconds
        self.exclude_paths = {
            path.rstrip("/") or "/"
            for path in split_csv(settings.cache_exclude_paths)
        }

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if not self.enabled or request.method != "GET":
            return await call_next(request)

        path = request.url.path.rstrip("/") or "/"
        if not _matches_cache_route(path):
            return await call_next(request)
        if path in self.exclude_paths:
            return await call_next(request)

        if request.headers.get("authorization") is not None:
            return await call_next(request)
        if request.headers.get("cookie") is not None:
            return await call_next(request)
        if getattr(request.state, "user", None):
            return await call_next(request)

        cache_key = self._cache_key(request.scope)
        redis = self.redis_provider()

        cached_body = await self._read_cache(redis, cache_key)
        if cached_body is not None:
            return self._cached_response(cached_body)

        response = await call_next(request)
        response.headers["X-Cache"] = "MISS"

        if not self._is_cacheable_response(response):
            return response

        return await self._forward_and_store(response, redis, cache_key)

    @staticmethod
    def _cache_key(scope: Scope) -> str:
        raw_path = scope.get("raw_path")
        if raw_path:
            path = raw_path.decode("latin-1")
        else:
            path = scope.get("path", "")

        query = scope.get("query_string", b"").decode("latin-1")
        key = f"{CACHE_KEY_PREFIX}{path}"
        return f"{key}?{query}" if query else key

    @staticmethod
    def _is_cacheable_response(response: Response) -> bool:
        if response.status_code != 200:
            return False

        headers = response.headers
        if not _is_json_content_type(headers.get("content-type")):
            return False
        if headers.get("set-cookie") is not None:
            return False

        vary = headers.get("vary", "")
        if any(value.strip() == "*" for value in vary.split(",")):
            return False

        cache_control = headers.get("cache-control", "").lower()
        return "no-store" not in cache_control and "private" not in cache_control

    async def _read_cache(
        self,
        redis: Redis,
        cache_key: str,
    ) -> bytes | None:
        try:
            raw = await redis.get(cache_key)
        except RedisError:
            logger.warning("Redis cache read failed; continuing without cache")
            return None

        if raw is None:
            return None
        if isinstance(raw, str):
            return raw.encode("utf-8")
        return raw

    @staticmethod
    def _cached_response(body: bytes) -> Response:
        return Response(
            content=body,
            status_code=200,
            media_type="application/json",
            headers={"X-Cache": "HIT"},
        )

    async def _forward_and_store(
        self,
        response: Response,
        redis: Redis,
        cache_key: str,
    ) -> Response:
        body = b"".join([chunk async for chunk in response.body_iterator])

        if len(body) <= MAX_CACHE_BODY_BYTES:
            await self._store_response(redis, cache_key, response, body)

        return self._buffered_response(response, body)

    @staticmethod
    def _buffered_response(response: Response, body: bytes) -> Response:
        buffered = Response(content=body, status_code=response.status_code)
        buffered.raw_headers = list(response.headers.raw)
        return buffered

    async def _store_response(
        self,
        redis: Redis,
        cache_key: str,
        response: Response,
        body: bytes,
    ) -> None:
        if not self._is_cacheable_response(response):
            return

        try:
            await redis.set(cache_key, body, ex=self.ttl_seconds)
        except RedisError:
            logger.warning("Redis cache write failed; response was not cached")
