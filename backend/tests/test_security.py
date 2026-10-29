"""Tests for JWT and signed-cookie helpers."""

import base64
import hashlib
import hmac
from urllib.parse import quote

import jwt
import pytest

from app.base_config import split_csv
from app.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    parse_duration,
    sign_cookie_value,
    unsign_cookie_value,
)
from tests.helpers import TEST_COOKIE_SECRET, build_settings


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("7m", 420),
        ("7d", 604800),
        ("30s", 30),
        ("500ms", 0.5),
        ("2h", 7200),
        ("1w", 604800),
        ("30", 0.03),
    ],
)
def test_parse_duration(value: str, seconds: float) -> None:
    assert parse_duration(value) == seconds


def test_parse_duration_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        parse_duration("soon")


def test_split_csv_trims_and_drops_blank_values() -> None:
    assert split_csv(" a, b ,,c ") == ["a", "b", "c"]
    assert split_csv("") == []
    assert split_csv(None) == []


def test_access_and_refresh_tokens_decode_with_expected_type() -> None:
    settings = build_settings()
    access = create_access_token("session-1", settings)
    refresh = create_refresh_token("session-1", settings)

    assert decode_token(access, settings)["type"] == "access"
    assert decode_token(refresh, settings)["type"] == "refresh"
    assert decode_token(access, settings)["sessionId"] == "session-1"


def test_token_signed_with_wrong_secret_is_rejected() -> None:
    settings = build_settings()
    token = create_access_token("session-1", settings)

    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(
            token,
            build_settings(
                jwt_secret="wrong-jwt-secret-that-is-long-enough-123456"
            ),
        )


def test_cookie_signature_is_express_compatible() -> None:
    value = "s:eyJhbGciOiJIUzI1NiJ9.signature"
    expected_digest = hmac.new(
        TEST_COOKIE_SECRET.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_signature = (
        base64.b64encode(expected_digest).decode("ascii").rstrip("=")
    )

    signed = sign_cookie_value(value, TEST_COOKIE_SECRET)

    assert signed == f"s:{value}.{expected_signature}"


def test_signed_cookie_round_trips_through_url_encoding() -> None:
    value = "s:eyJhbGciOiJIUzI1NiJ9.payload+signature"
    signed = sign_cookie_value(value, TEST_COOKIE_SECRET)
    encoded = quote(signed, safe="")

    assert encoded != signed
    assert unsign_cookie_value(encoded, TEST_COOKIE_SECRET) == value


def test_unsigned_or_tampered_cookie_is_rejected() -> None:
    value = "s:payload.signature"

    assert unsign_cookie_value("payload.signature", TEST_COOKIE_SECRET) is None
    assert (
        unsign_cookie_value(
            sign_cookie_value(
                value,
                "wrong-cookie-secret-that-is-long-enough-123456",
            ),
            TEST_COOKIE_SECRET,
        )
        is None
    )
