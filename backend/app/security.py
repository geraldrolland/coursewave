"""Password, token, and signed-cookie helpers for the CourseWave service."""

import base64
import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote

import jwt
from pwdlib import PasswordHash

from app.base_config import Settings

password_hasher = PasswordHash.recommended()

_DURATION_PATTERN = re.compile(r"^(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|m|h|d|w)?$")
_DURATION_UNITS = {
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 604800.0,
    None: 0.001,
}


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2."""

    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a stored hash."""

    return password_hasher.verify(password, password_hash)


def parse_duration(value: str | int | float) -> float:
    """Parse a duration such as ``7m`` or ``500ms`` into seconds.

    Bare numbers are treated as milliseconds, matching the gateway settings
    format.
    """

    if isinstance(value, (int, float)):
        return float(value) / 1000.0

    match = _DURATION_PATTERN.fullmatch(value.strip().lower())
    if match is None:
        raise ValueError(f"Invalid duration: {value!r}")

    amount = float(match.group("value"))
    return amount * _DURATION_UNITS[match.group("unit")]


def _create_token(session_id: str, token_type: str, expiry: str, secret: str) -> str:
    """Create a signed HS256 JWT for the given session."""

    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sessionId": session_id,
        "type": token_type,
        "iat": now,
        "exp": now + timedelta(seconds=parse_duration(expiry)),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def create_access_token(session_id: str, settings: Settings) -> str:
    """Create a short-lived access token."""

    return _create_token(
        session_id,
        "access",
        settings.access_token_expiry,
        settings.jwt_secret,
    )


def create_refresh_token(session_id: str, settings: Settings) -> str:
    """Create a long-lived refresh token."""

    return _create_token(
        session_id,
        "refresh",
        settings.refresh_token_expiry,
        settings.jwt_secret,
    )


def decode_token(token: str, settings: Settings) -> dict[str, Any]:
    """Verify an HS256 JWT and return its payload."""

    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        options={"require": ["exp"]},
    )
    return dict(payload)


def sign_cookie_value(value: str, secret: str) -> str:
    """Sign a cookie value in an Express ``cookie-signature`` compatible form."""

    digest = hmac.new(
        secret.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"s:{value}.{signature}"


def unsign_cookie_value(signed_value: str, secret: str) -> str | None:
    """Verify and decode an Express-signed cookie value.

    Cookie values are URL-decoded first because Express URL-encodes the signed
    value when serializing the ``Set-Cookie`` header. Returns ``None`` when the
    value is malformed or the signature does not match.
    """

    value = unquote(signed_value)
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]

    if not value.startswith("s:"):
        return None

    value = value[2:]
    unsigned, separator, signature = value.rpartition(".")
    if not separator or not unsigned or not signature:
        return None

    expected = sign_cookie_value(unsigned, secret).rsplit(".", 1)[1]
    if not hmac.compare_digest(expected, signature):
        return None

    return unsigned
