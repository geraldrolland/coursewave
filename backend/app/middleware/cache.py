"""ASGI middleware that caches public GET responses in Redis."""

import base64
import json
import logging
from collections.abc import Callable
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


class CacheMiddleware:
    """Cache only unauthenticated, cookie-free GET responses."""

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        redis_provider: Callable[[], Redis],
    ) -> None:
        self.app = app
        self.redis_provider = redis_provider
        self.enabled = settings.cache_enabled
        self.ttl_seconds = settings.cache_ttl_seconds
        self.exclude_paths = {
            path.rstrip("/") or "/"
            for path in split_csv(settings.cache_exclude_paths)
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        if scope.get("method") != "GET":
            await self.app(scope, receive, send)
            return

        path = (scope.get("path") or "").rstrip("/") or "/"
        if path in self.exclude_paths:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if headers.get("authorization") is not None:
            await self.app(scope, receive, send)
            return
        if headers.get("cookie") is not None:
            await self.app(scope, receive, send)
            return

        state = scope.get("state")
        if isinstance(state, dict) and state.get("user"):
            await self.app(scope, receive, send)
            return

        cache_key = self._cache_key(scope)
        redis = self.redis_provider()

        cached_response = await self._read_cache(redis, cache_key)
        if cached_response is not None:
            await self._send_cached(send, *cached_response)
            return

        await self._forward_and_store(scope, receive, send, redis, cache_key)

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
    async def _send_cached(
        send: Send,
        status: int,
        headers: list[tuple[bytes, bytes]],
        body: bytes,
    ) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def _forward_and_store(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        redis: Redis,
        cache_key: str,
    ) -> None:
        start_message: Message | None = None
        body_chunks: list[bytes] = []
        body_size = 0
        buffering = True

        async def send_wrapper(message: Message) -> None:
            nonlocal start_message, body_size, buffering

            if message["type"] == "http.response.start":
                start_message = message
                buffering = _is_json_content_type(
                    Headers(raw=message.get("headers", [])).get("content-type")
                )
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Cache"] = "MISS"
                await send(message)
                return

            if message["type"] != "http.response.body":
                await send(message)
                return

            chunk = message.get("body", b"")
            more_body = bool(message.get("more_body", False))

            if not buffering:
                await send(message)
                return

            body_chunks.append(chunk)
            body_size += len(chunk)

            if more_body:
                # Multi-chunk responses are treated as streaming and are not
                # buffered for caching.
                buffering = False
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"".join(body_chunks),
                        "more_body": True,
                    }
                )
                body_chunks.clear()
                return

            if body_size > MAX_CACHE_BODY_BYTES:
                buffering = False
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"".join(body_chunks),
                        "more_body": True,
                    }
                )
                body_chunks.clear()
                await send({"type": "http.response.body", "body": b""})
                return

            buffering = False
            body = b"".join(body_chunks)
            await send({"type": "http.response.body", "body": body})
            await self._store_response(
                redis,
                cache_key,
                start_message,
                body,
            )

        await self.app(scope, receive, send_wrapper)

    async def _store_response(
        self,
        redis: Redis,
        cache_key: str,
        start_message: Message | None,
        body: bytes,
    ) -> None:
        if start_message is None or start_message.get("status") != 200:
            return

        headers = Headers(raw=start_message.get("headers", []))
        if not _is_json_content_type(headers.get("content-type")):
            return
        if headers.get("set-cookie") is not None:
            return

        vary = headers.get("vary", "")
        if any(value.strip() == "*" for value in vary.split(",")):
            return

        cache_control = headers.get("cache-control", "").lower()
        if "no-store" in cache_control or "private" in cache_control:
            return

        stored_headers = [
            [name.decode("latin-1"), value.decode("latin-1")]
            for name, value in start_message.get("headers", [])
            if name.lower() not in EXCLUDED_RESPONSE_HEADERS
        ]
        payload = json.dumps(
            {
                "status": start_message["status"],
                "headers": stored_headers,
                "body": base64.b64encode(body).decode("ascii"),
            }
        )

        try:
            await redis.set(cache_key, payload, ex=self.ttl_seconds)
        except RedisError:
            logger.warning("Redis cache write failed; response was not cached")
