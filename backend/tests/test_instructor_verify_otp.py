"""Tests for instructor OTP verification."""

import json
import time
from uuid import uuid4

import jwt
import pytest
from redis.exceptions import RedisError

from app.security import hash, unsign_cookie_value
from app.services.instructor.utils import (
    AUTH_UNAVAILABLE,
    INSTRUCTOR_NOT_FOUND,
    INVALID_OTP_CODE,
    INVALID_OTP_TOKEN,
    OTP_CODE_TTL_SECONDS,
    OTP_TOKEN_TTL_SECONDS,
)
from tests.fakes import FakeRedis
from tests.helpers import TEST_COOKIE_SECRET, TEST_JWT_SECRET
from tests.instructor_fakes import (
    DEFAULT_EMAIL,
    FakeInstructorSession,
    instructor_client,
    make_instructor,
)

VERIFY_PATH = "/instructor/api/v1/verify-otp"
REFRESH_PATH = "/auth/api/v1/token-refresh"
TOKEN = "otp-challenge-token"
CODE = "000042"


def seed_challenge(
    redis: FakeRedis,
    instructor_id: object,
    code: str = CODE,
    expires_in: int = OTP_CODE_TTL_SECONDS,
) -> None:
    """Store a hashed OTP challenge for an instructor."""

    redis.seed(
        TOKEN,
        json.dumps(
            {
                "instructor_id": str(instructor_id),
                "code_hash": hash(code),
                "exp": int(time.time()) + expires_in,
            }
        ),
        ttl=OTP_TOKEN_TTL_SECONDS,
    )


def test_verify_otp_returns_tokens_and_creates_instructor_session() -> None:
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_challenge(redis, instructor.instructor_id)

    with instructor_client(session, redis) as client:
        response = client.post(
            VERIFY_PATH,
            json={"code": CODE, "token": f"  {TOKEN}  "},
        )

        assert response.status_code == 200
        assert set(response.json()) == {"accessToken"}
        assert "location" not in response.headers

        access_payload = jwt.decode(
            response.json()["accessToken"],
            TEST_JWT_SECRET,
            algorithms=["HS256"],
        )
        assert access_payload["type"] == "access"
        session_id = access_payload["sessionId"]
        session_key = f"session:{session_id}"

        assert redis.values[session_key] == json.dumps(
            {
                "id": str(instructor.instructor_id),
                "email": DEFAULT_EMAIL,
                "role": "instructor",
            }
        )
        assert redis.ttls[session_key] == 604800
        assert TOKEN not in redis.values
        assert TOKEN in redis.deleted

        set_cookie = response.headers["set-cookie"]
        assert set_cookie.startswith("refresh=")
        assert "HttpOnly" in set_cookie
        assert "Secure" in set_cookie
        assert "SameSite=Strict" in set_cookie
        assert "Path=/auth/api/v1/token-refresh" in set_cookie
        assert "Max-Age=604800" in set_cookie

        cookie_value = set_cookie.split(";", 1)[0].split("=", 1)[1]
        refresh_token = unsign_cookie_value(
            cookie_value,
            TEST_COOKIE_SECRET,
        )
        assert refresh_token is not None
        refresh_payload = jwt.decode(
            refresh_token,
            TEST_JWT_SECRET,
            algorithms=["HS256"],
        )
        assert refresh_payload["type"] == "refresh"
        assert refresh_payload["sessionId"] == session_id

        cookie_header = set_cookie.split(";", 1)[0]
        refresh_response = client.post(
            REFRESH_PATH,
            headers={"Cookie": cookie_header},
        )

    assert refresh_response.status_code == 200
    assert refresh_response.json()["accessToken"]


def test_verify_otp_wrong_code_keeps_challenge() -> None:
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_challenge(redis, instructor.instructor_id)

    with instructor_client(session, redis) as client:
        response = client.post(
            VERIFY_PATH,
            json={"code": "123456", "token": TOKEN},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_OTP_CODE}
    assert TOKEN in redis.values
    assert TOKEN not in redis.deleted
    assert not any(key.startswith("session:") for key in redis.values)
    assert response.headers.get("set-cookie") is None


def test_verify_otp_missing_challenge_returns_400() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.post(
            VERIFY_PATH,
            json={"code": CODE, "token": TOKEN},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_OTP_TOKEN}
    assert redis.values == {}


def test_verify_otp_expired_challenge_returns_403_and_keeps_token() -> None:
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_challenge(redis, instructor.instructor_id, expires_in=-1)

    with instructor_client(session, redis) as client:
        response = client.post(
            VERIFY_PATH,
            json={"code": CODE, "token": TOKEN},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": INVALID_OTP_TOKEN}
    assert TOKEN in redis.values
    assert TOKEN not in redis.deleted
    assert not any(key.startswith("session:") for key in redis.values)
    assert response.headers.get("set-cookie") is None


def test_verify_otp_rejects_legacy_plaintext_challenge() -> None:
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    redis.seed(
        TOKEN,
        json.dumps(
            {
                "instructor_id": str(instructor.instructor_id),
                "otp_code": CODE,
            }
        ),
        ttl=OTP_TOKEN_TTL_SECONDS,
    )

    with instructor_client(session, redis) as client:
        response = client.post(
            VERIFY_PATH,
            json={"code": CODE, "token": TOKEN},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_OTP_TOKEN}
    assert TOKEN in redis.values
    assert not any(key.startswith("session:") for key in redis.values)


def test_verify_otp_malformed_challenge_returns_400() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()
    redis.seed(TOKEN, "{not-json", ttl=120)

    with instructor_client(session, redis) as client:
        response = client.post(
            VERIFY_PATH,
            json={"code": CODE, "token": TOKEN},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_OTP_TOKEN}
    assert TOKEN in redis.values


def test_verify_otp_malformed_instructor_id_returns_400() -> None:
    session = FakeInstructorSession()
    redis = FakeRedis()
    seed_challenge(redis, "not-a-uuid")

    with instructor_client(session, redis) as client:
        response = client.post(
            VERIFY_PATH,
            json={"code": CODE, "token": TOKEN},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_OTP_TOKEN}
    assert TOKEN in redis.values


def test_verify_otp_unknown_instructor_returns_404() -> None:
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_challenge(redis, uuid4())

    with instructor_client(session, redis) as client:
        response = client.post(
            VERIFY_PATH,
            json={"code": CODE, "token": TOKEN},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": INSTRUCTOR_NOT_FOUND}
    assert TOKEN in redis.values
    assert not any(key.startswith("session:") for key in redis.values)


def test_verify_otp_redis_read_failure_returns_503() -> None:
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_challenge(redis, instructor.instructor_id)
    redis.fail("get", RedisError("redis unavailable"))

    with instructor_client(session, redis) as client:
        response = client.post(
            VERIFY_PATH,
            json={"code": CODE, "token": TOKEN},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": AUTH_UNAVAILABLE}


def test_verify_otp_redis_delete_failure_returns_503() -> None:
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_challenge(redis, instructor.instructor_id)
    redis.fail("delete", RedisError("redis unavailable"))

    with instructor_client(session, redis) as client:
        response = client.post(
            VERIFY_PATH,
            json={"code": CODE, "token": TOKEN},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": AUTH_UNAVAILABLE}
    assert TOKEN in redis.values
    assert not any(key.startswith("session:") for key in redis.values)


def test_verify_otp_session_failure_returns_503() -> None:
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_challenge(redis, instructor.instructor_id)
    redis.fail("set", RedisError("redis unavailable"))

    with instructor_client(session, redis) as client:
        response = client.post(
            VERIFY_PATH,
            json={"code": CODE, "token": TOKEN},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": AUTH_UNAVAILABLE}
    assert TOKEN not in redis.values
    assert not any(key.startswith("session:") for key in redis.values)


@pytest.mark.parametrize(
    "payload",
    [
        {"code": CODE, "token": TOKEN, "role": "instructor"},
        {"code": "12345", "token": TOKEN},
        {"code": "12345a", "token": TOKEN},
        {"token": TOKEN},
        {"code": CODE},
    ],
)
def test_verify_otp_rejects_invalid_payloads(
    payload: dict[str, str],
) -> None:
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_challenge(redis, instructor.instructor_id)

    with instructor_client(session, redis) as client:
        response = client.post(VERIFY_PATH, json=payload)

    assert response.status_code == 422
    assert TOKEN in redis.values
    assert not any(key.startswith("session:") for key in redis.values)
