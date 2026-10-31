"""BaseHTTPMiddleware that caches public GET responses in Redis."""

import base64
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.types import ASGIApp, Scope

from app.base_config import Settings, split_csv

logger = logging.getLogger("backend.cache")

CACHE_KEY_PREFIX = "cache:"
MAX_CACHE_BODY_BYTES = 1024 * 1024
EXCLUDED_RESPONSE_HEADERS = frozenset(
    {
        b"access-control-allow-credentials",
        b"access-control-allow-headers",
        b"access-control-allow-methods",
        b"access-control-allow-origin",
        b"access-control-expose-headers",
        b"access-control-max-age",
        b"connection",
        b"content-length",
        b"keep-alive",
        b"retry-after",
        b"transfer-encoding",
        b"upgrade",
        b"vary",
        b"x-cache",
        b"x-ratelimit-limit",
        b"x-ratelimit-remaining",
        b"x-ratelimit-reset",
        b"x-request-id",
    }
)


def _is_json_content_type(value: str | None) -> bool:
    """Return whether ``value`` is ``application/json``, ignoring parameters."""

    if not value:
        return False
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type == "application/json"


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

        cached_response = await self._read_cache(redis, cache_key)
        if cached_response is not None:
            return self._cached_response(*cached_response)

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
    ) -> tuple[int, list[tuple[bytes, bytes]], bytes] | None:
        try:
            raw = await redis.get(cache_key)
        except RedisError:
            logger.warning("Redis cache read failed; continuing without cache")
            return None

        if raw is None:
            return None

        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            payload: dict[str, Any] = json.loads(raw)
            status = int(payload["status"])
            stored_headers = payload["headers"]
            body = base64.b64decode(payload["body"])
        except (KeyError, TypeError, ValueError):
            logger.warning("Ignoring malformed cached response for %s", cache_key)
            return None

        headers: list[tuple[bytes, bytes]] = []
        for name, value in stored_headers:
            encoded_name = str(name).encode("latin-1").lower()
            if encoded_name in EXCLUDED_RESPONSE_HEADERS:
                continue
            headers.append(
                (str(name).encode("latin-1"), str(value).encode("latin-1"))
            )

        if not _is_json_content_type(Headers(raw=headers).get("content-type")):
            logger.warning("Ignoring non-JSON cached response for %s", cache_key)
            return None

        headers.append((b"x-cache", b"HIT"))
        headers.append((b"content-length", str(len(body)).encode("latin-1")))
        return status, headers, body

    @staticmethod
    def _cached_response(
        status: int,
        headers: list[tuple[bytes, bytes]],
        body: bytes,
    ) -> Response:
        response = Response(content=body, status_code=status)
        response.raw_headers = headers
        return response

    async def _forward_and_store(
        self,
        response: Response,
        redis: Redis,
        cache_key: str,
    ) -> Response:
        body_iterator = response.body_iterator
        first_chunk = await self._next_chunk(body_iterator)

        if first_chunk is None:
            await self._store_response(redis, cache_key, response, b"")
            return self._buffered_response(response, b"")

        if len(first_chunk) > MAX_CACHE_BODY_BYTES:
            return self._stream_response(
                response,
                [first_chunk],
                body_iterator,
            )

        second_chunk = await self._next_chunk(body_iterator)
        if second_chunk is not None:
            return self._stream_response(
                response,
                [first_chunk, second_chunk],
                body_iterator,
            )

        await self._store_response(redis, cache_key, response, first_chunk)
        return self._buffered_response(response, first_chunk)

    @staticmethod
    async def _next_chunk(body_iterator: AsyncIterator[bytes]) -> bytes | None:
        try:
            return await body_iterator.__anext__()
        except StopAsyncIteration:
            return None

    @staticmethod
    def _buffered_response(response: Response, body: bytes) -> Response:
        buffered = Response(content=body, status_code=response.status_code)
        buffered.raw_headers = list(response.headers.raw)
        return buffered

    @staticmethod
    def _stream_response(
        response: Response,
        buffered_chunks: list[bytes],
        body_iterator: AsyncIterator[bytes],
    ) -> StreamingResponse:
        async def stream_body() -> AsyncIterator[bytes]:
            for chunk in buffered_chunks:
                yield chunk
            async for chunk in body_iterator:
                yield chunk

        streaming = StreamingResponse(
            stream_body(),
            status_code=response.status_code,
        )
        streaming.raw_headers = list(response.headers.raw)
        return streaming

    async def _store_response(
        self,
        redis: Redis,
        cache_key: str,
        response: Response,
        body: bytes,
    ) -> None:
        if not self._is_cacheable_response(response):
            return

        stored_headers = [
            [name.decode("latin-1"), value.decode("latin-1")]
            for name, value in response.headers.raw
            if name.lower() not in EXCLUDED_RESPONSE_HEADERS
        ]
        payload = json.dumps(
            {
                "status": response.status_code,
                "headers": stored_headers,
                "body": base64.b64encode(body).decode("ascii"),
            }
        )

        try:
            await redis.set(cache_key, payload, ex=self.ttl_seconds)
        except RedisError:
            logger.warning("Redis cache write failed; response was not cached")
