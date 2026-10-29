"""Strategy-to-handler registry for rate limiting."""

from collections.abc import Awaitable, Callable

from starlette.types import Scope

from app.middleware.ratelimit.fixed_window import fixed_window_limit
from app.middleware.ratelimit.token_bucket import token_bucket_limit

RateLimiterHandler = Callable[[Scope, int], Awaitable[bool]]

RATELIMITER_REGISTRY: dict[str, RateLimiterHandler] = {
    "fixed_window": fixed_window_limit,
    "token_bucket": token_bucket_limit,
}
