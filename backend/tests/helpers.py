"""Shared helpers for middleware tests."""

import hashlib
import hmac
import time

TEST_PROXY_SECRET = "test-proxy-secret"


def sign_request(
    method: str,
    url: str,
    timestamp: int,
    secret: str = TEST_PROXY_SECRET,
) -> str:
    """Create the same HMAC-SHA256 signature the gateway sends."""

    payload = f"{method}:{url}:{timestamp}"
    return hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def signed_headers(
    method: str,
    url: str,
    timestamp: int | None = None,
    secret: str = TEST_PROXY_SECRET,
) -> dict[str, str]:
    """Build gateway signature headers for a test request."""

    timestamp = int(time.time() * 1000) if timestamp is None else timestamp
    return {
        "x-proxy-signature": sign_request(method, url, timestamp, secret),
        "x-proxy-timestamp": str(timestamp),
    }
