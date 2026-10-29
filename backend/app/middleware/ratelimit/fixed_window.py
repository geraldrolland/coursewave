"""Redis fixed-window rate limiter handler."""

import math
from collections.abc import Callable

from redis.asyncio import Redis
from starlette.types import Scope

from app.middleware.ratelimit.utils import RateLimitResult


async def fixed_window_limit(
    request: Scope,
    window_ms: int,
    *,
    redis_provider: Callable[[], Redis],
    max_requests: int,
) -> bool:
    """Count requests in a fixed window using Redis INCR/EXPIRE.

    The handler receives the request and the route window. Redis and the
    request cap are bound by the middleware, which keeps the handler contract
    small while still allowing the global ``RATE_LIMIT_MAX`` setting to control
    the limit.
    """

    state = request.setdefault("state", {})
    context = state["ratelimit"]
    key = str(context["key"])

    window_ms = max(1, int(window_ms))
    max_requests = max(1, int(max_requests))
    window_seconds = max(1, math.ceil(window_ms / 1000))

    redis = redis_provider()
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, window_seconds)

    ttl = int(await redis.ttl(key))
    if ttl < 0:
        await redis.expire(key, window_seconds)
        ttl = window_seconds

    remaining = max(0, max_requests - count)
    allowed = count <= max_requests
    result = RateLimitResult(
        limit=max_requests,
        remaining=remaining,
        reset_seconds=ttl,
        allowed=allowed,
        retry_after_seconds=ttl if not allowed else 0,
    )
    state["ratelimit_result"] = result
    return allowed
