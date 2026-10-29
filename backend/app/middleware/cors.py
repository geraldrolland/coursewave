"""CORS configuration matching the former gateway environment variables."""

from typing import Any

from app.base_config import Settings, split_csv

DEFAULT_EXPOSE_HEADERS = [
    "X-Request-ID",
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
    "Retry-After",
    "X-Cache",
]


def cors_middleware_kwargs(settings: Settings) -> dict[str, Any]:
    """Build keyword arguments for Starlette's ``CORSMiddleware``."""

    origins = split_csv(settings.cors_origin) or ["*"]
    methods = split_csv(settings.cors_methods) or ["*"]
    headers = split_csv(settings.cors_headers) or ["*"]

    return {
        "allow_origins": origins,
        "allow_methods": methods,
        "allow_headers": headers,
        "allow_credentials": settings.cors_credentials,
        "expose_headers": DEFAULT_EXPOSE_HEADERS,
    }
