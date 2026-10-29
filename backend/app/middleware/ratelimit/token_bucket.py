"""Redis token-bucket rate limiter handler."""

import math
import time
from collections.abc import Callable

from redis.asyncio import Redis
from starlette.types import Scope

from app.middleware.ratelimit.utils import RateLimitResult

TOKEN_BUCKET_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now_ms = tonumber(ARGV[3])
local ttl_ms = tonumber(ARGV[4])

local state = redis.call('HMGET', key, 'tokens', 'timestamp')
local tokens = tonumber(state[1])
local timestamp = tonumber(state[2])

if tokens == nil then
  tokens = capacity
  timestamp = now_ms
end

local elapsed = math.max(0, now_ms - timestamp) / 1000
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end

redis.call(
  'HSET',
  key,
  'tokens',
  tostring(tokens),
  'timestamp',
  tostring(now_ms)
)
redis.call('PEXPIRE', key, ttl_ms)

return {allowed, math.floor(tokens * 1000)}
"""


async def token_bucket_limit(
    request: Scope,
    window_ms: int,
    *,
    redis_provider: Callable[[], Redis],
    max_requests: int,
) -> bool:
    """Allow bursts up to ``max_requests`` and refill over the route window."""

    state = request.setdefault("state", {})
    context = state["ratelimit"]
    key = str(context["key"])

    capacity = max(1, int(max_requests))
    window_ms = max(1, int(window_ms))
    ttl_ms = max(1000, window_ms)
    refill_rate_per_second = capacity / max(window_ms / 1000.0, 1.0)

    now_ms = int(time.time() * 1000)
    result = await redis_provider().eval(
        TOKEN_BUCKET_SCRIPT,
        1,
        key,
        capacity,
        refill_rate_per_second,
        now_ms,
        ttl_ms,
    )
    allowed = bool(int(result[0]))
    tokens = max(0.0, float(result[1]) / 1000.0)
    remaining = int(math.floor(tokens))

    if refill_rate_per_second > 0:
        reset_seconds = max(
            0,
            math.ceil((capacity - tokens) / refill_rate_per_second),
        )
        retry_after_seconds = max(
            1,
            math.ceil(max(0.001, 1.0 - tokens) / refill_rate_per_second),
        )
    else:
        reset_seconds = 0
        retry_after_seconds = ttl_ms // 1000

    rate_result = RateLimitResult(
        limit=capacity,
        remaining=remaining,
        reset_seconds=reset_seconds,
        allowed=allowed,
        retry_after_seconds=0 if allowed else retry_after_seconds,
    )
    state["ratelimit_result"] = rate_result
    return allowed
