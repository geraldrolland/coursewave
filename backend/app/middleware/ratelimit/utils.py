"""Shared helpers for rate limiting."""

from dataclasses import dataclass

from starlette.types import Scope


@dataclass(frozen=True)
class RateLimitResult:
    """Outcome of one rate-limit check."""

    limit: int
    remaining: int
    reset_seconds: int
    allowed: bool
    retry_after_seconds: int = 0


def client_identifier(scope: Scope) -> str:
    """Return the authenticated user ID, or the socket IP for anonymous callers."""

    state = scope.get("state")
    if isinstance(state, dict):
        user = state.get("user")
        if isinstance(user, dict):
            user_id = user.get("userId")
            if user_id is None:
                user_id = user.get("user_id")
            if user_id is not None and str(user_id):
                return str(user_id)

    client = scope.get("client")
    if client:
        return str(client[0])

    return "unknown"


def rate_limit_headers(result: RateLimitResult) -> dict[str, str]:
    """Build the response headers shared by all rate-limit strategies."""

    headers = {
        "X-RateLimit-Limit": str(result.limit),
        "X-RateLimit-Remaining": str(max(0, result.remaining)),
        "X-RateLimit-Reset": str(max(0, result.reset_seconds)),
    }
    if not result.allowed:
        headers["Retry-After"] = str(max(1, result.retry_after_seconds))

    return headers
