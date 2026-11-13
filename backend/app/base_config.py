"""Application configuration for the CourseWave service."""

from functools import lru_cache
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_AUTH_EXCLUDE_PATHS = (
    "/health,"
    "/auth/api/v1/token-refresh,"
    "/student/api/v1/login,"
    "/student/api/v1/register,"
    "/student/api/v1/email,"
    "/student/api/v1/password-reset,"
    "/student/api/v1/verify-email,"
    "/student/api/v1/google,"
    "/instructor/api/v1/register,"
    "/instructor/api/v1/login,"
    "/instructor/api/v1/verify-otp,"
    "/instructor/api/v1/resend-otp,"
    "/instructor/api/v1/google,"
    "/instructor/api/v1/verify-email,"
    "/instructor/api/v1/email,"
    "/instructor/api/v1/password-reset"
)

DEFAULT_RATELIMIT_ROUTES: list[dict[str, Any]] = [
    {"prefix": "/student/api", "window_ms": 90000, "strategy": "fixed_window"},
    {"prefix": "*", "window_ms": 90000, "strategy": "fixed_window"},
]


def split_csv(value: str | None) -> list[str]:
    """Split a comma-separated setting into trimmed, non-empty values."""

    if not value:
        return []

    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or ``.env``."""

    log_level: str = "INFO"
    app_host: str = "localhost:3000"
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0
    auto_create_tables: bool = False
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    sendgrid_api_key: str = "SENDGRID_API_KEY_PLACEHOLDER"
    sendgrid_from_email: str = "no-reply@coursewave.example"
    sendgrid_from_name: str = "CourseWave"

    notification_topic: str = "notification"
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_consumer_enabled: bool = False
    kafka_consumer_group_id: str = "coursewave-notification-websockets"

    termii_api_key: str = "TERMII_API_KEY_PLACEHOLDER"
    termii_sender_id: str = "TERMII_SENDER_ID_PLACEHOLDER"
    termii_channel: str = "generic"
    termii_message_type: str = "plain"

    minio_endpoint: str = "localhost:9000"
    minio_public_url: str = "http://localhost:9000"
    minio_access_key: str = "coursewave"
    minio_secret_key: str = "coursewave-secret"
    minio_bucket: str = "profilepics"
    minio_secure: bool = False

    google_client_id: str = "GOOGLE_CLIENT_ID_PLACEHOLDER"
    google_client_secret: str = "GOOGLE_CLIENT_SECRET_PLACEHOLDER"
    google_redirect_uri: str = "GOOGLE_REDIRECT_URI_PLACEHOLDER"

    jwt_secret: str = "change-me-in-production"
    cookie_secret: str = "change-me-in-production"
    access_token_expiry: str = "7m"
    refresh_token_expiry: str = "7d"
    refresh_token_max_age_seconds: int = 604800
    auth_exclude_paths: str = DEFAULT_AUTH_EXCLUDE_PATHS

    cors_origin: str = "*"
    cors_methods: str = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
    cors_headers: str = "Content-Type,Authorization,X-Request-ID"
    cors_credentials: bool = False

    cache_enabled: bool = True
    cache_ttl_seconds: int = 420
    cache_exclude_paths: str = "/health"

    rate_limit_enabled: bool = True
    rate_limit_max: int = 100
    rate_limit_exclude_paths: str = "/health"
    ratelimit_routes: list[dict[str, Any]] = Field(
        default_factory=lambda: [dict(route) for route in DEFAULT_RATELIMIT_ROUTES]
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached settings instance."""

    return Settings()
