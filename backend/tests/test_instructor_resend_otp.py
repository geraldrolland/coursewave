"""Tests for the instructor OTP resend endpoint."""

import json
import time
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.security import hash, verify_hash
from app.services.instructor.utils import (
    AUTH_UNAVAILABLE,
    INSTRUCTOR_NOT_FOUND,
    OTP_CODE_TTL_SECONDS,
    OTP_NOT_EXPIRED,
    OTP_RESENT,
    OTP_SESSION_EXPIRED,
    OTP_TOKEN_TTL_SECONDS,
)
from app.services.notification.model import (
    Notification,
    NotificationAudience,
    NotificationType,
)
from app.services.outbox.model import Outbox, OutboxStatus
from tests.fakes import FakeRedis
from tests.instructor_fakes import (
    DEFAULT_EMAIL,
    FakeInstructorSession,
    instructor_client,
    make_instructor,
)

RESEND_PATH = "/instructor/api/v1/resend-otp"
TOKEN = "otp-challenge-token"
OLD_CODE = "111111"
NEW_CODE = "000042"


def seed_challenge(
    redis: FakeRedis,
    instructor_id: object,
    *,
    code: str = OLD_CODE,
    expires_in: int = -1,
    ttl: int = OTP_TOKEN_TTL_SECONDS,
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
        ttl=ttl,
    )


def test_resend_otp_replaces_expired_challenge_and_queues_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.instructor.utils.secrets.randbelow",
        lambda _: 42,
    )
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_challenge(redis, instructor.instructor_id)
    before = int(time.time())

    with instructor_client(session, redis) as client:
        response = client.post(
            RESEND_PATH,
            json={"token": f"  {TOKEN}  "},
        )

    after = int(time.time())

    assert response.status_code == 200
    assert response.json() == {"message": OTP_RESENT}
    assert len(redis.values) == 1
    assert redis.ttls[TOKEN] == OTP_TOKEN_TTL_SECONDS
    assert OTP_TOKEN_TTL_SECONDS == 86400

    challenge = json.loads(redis.values[TOKEN])
    assert set(challenge) == {"instructor_id", "code_hash", "exp"}
    assert challenge["instructor_id"] == str(instructor.instructor_id)
    assert verify_hash(NEW_CODE, challenge["code_hash"])
    assert not verify_hash(OLD_CODE, challenge["code_hash"])
    assert (
        before + OTP_CODE_TTL_SECONDS
        <= challenge["exp"]
        <= after + OTP_CODE_TTL_SECONDS
    )
    assert not any(key.startswith("session:") for key in redis.values)

    notifications = [
        model for model in session.added if isinstance(model, Notification)
    ]
    outboxes = [model for model in session.added if isinstance(model, Outbox)]
    assert len(notifications) == 1
    assert len(outboxes) == 1
    notification = notifications[0]
    outbox = outboxes[0]
    assert notification.type is NotificationType.EMAIL
    assert notification.belongs_to is NotificationAudience.INSTRUCTOR
    assert notification.user_id == instructor.instructor_id
    assert notification.payload == {
        "template_type": "otp",
        "content": {"otp_code": NEW_CODE},
        "to_email": DEFAULT_EMAIL,
    }
    assert outbox.status is OutboxStatus.PENDING
    assert outbox.table == "notification"
    assert outbox.ref_id == str(notification.notification_id)
    assert session.commits == 1


def test_resend_otp_missing_token_returns_403() -> None:
    session = FakeInstructorSession(make_instructor(is_2fa_enabled=True))
    redis = FakeRedis()

    with instructor_client(session, redis) as client:
        response = client.post(RESEND_PATH, json={"token": TOKEN})

    assert response.status_code == 403
    assert response.json() == {"detail": OTP_SESSION_EXPIRED}
    assert session.added == []
    assert session.commits == 0
    assert redis.values == {}


def test_resend_otp_valid_token_returns_403_without_changing_challenge() -> None:
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_challenge(redis, instructor.instructor_id, expires_in=120)
    original = redis.values[TOKEN]

    with instructor_client(session, redis) as client:
        response = client.post(RESEND_PATH, json={"token": TOKEN})

    assert response.status_code == 403
    assert response.json() == {"detail": OTP_NOT_EXPIRED}
    assert redis.values[TOKEN] == original
    assert redis.ttls[TOKEN] == OTP_TOKEN_TTL_SECONDS
    assert session.added == []
    assert session.commits == 0


@pytest.mark.parametrize(
    "stored",
    [
        "{not-json",
        json.dumps({"instructor_id": str(uuid4()), "otp_code": OLD_CODE}),
        json.dumps(
            {
                "instructor_id": "not-a-uuid",
                "code_hash": hash(OLD_CODE),
                "exp": 1,
            }
        ),
    ],
)
def test_resend_otp_malformed_challenge_returns_403(stored: str) -> None:
    session = FakeInstructorSession(make_instructor(is_2fa_enabled=True))
    redis = FakeRedis()
    redis.seed(TOKEN, stored, ttl=OTP_TOKEN_TTL_SECONDS)

    with instructor_client(session, redis) as client:
        response = client.post(RESEND_PATH, json={"token": TOKEN})

    assert response.status_code == 403
    assert response.json() == {"detail": OTP_SESSION_EXPIRED}
    assert session.added == []
    assert session.commits == 0


def test_resend_otp_unknown_instructor_returns_404() -> None:
    session = FakeInstructorSession(make_instructor(is_2fa_enabled=True))
    redis = FakeRedis()
    seed_challenge(redis, uuid4())

    with instructor_client(session, redis) as client:
        response = client.post(RESEND_PATH, json={"token": TOKEN})

    assert response.status_code == 404
    assert response.json() == {"detail": INSTRUCTOR_NOT_FOUND}
    assert session.added == []
    assert session.commits == 0


def test_resend_otp_redis_read_failure_returns_503() -> None:
    session = FakeInstructorSession(make_instructor(is_2fa_enabled=True))
    redis = FakeRedis()
    redis.fail("get", RedisError("redis unavailable"))

    with instructor_client(session, redis) as client:
        response = client.post(RESEND_PATH, json={"token": TOKEN})

    assert response.status_code == 503
    assert response.json() == {"detail": AUTH_UNAVAILABLE}
    assert session.added == []
    assert session.commits == 0


def test_resend_otp_redis_write_failure_returns_503_and_cleans_up() -> None:
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_challenge(redis, instructor.instructor_id)
    redis.fail("set", RedisError("redis unavailable"))

    with instructor_client(session, redis) as client:
        response = client.post(RESEND_PATH, json={"token": TOKEN})

    assert response.status_code == 503
    assert response.json() == {"detail": AUTH_UNAVAILABLE}
    assert session.rollbacks == 1
    assert session.added == []
    assert TOKEN not in redis.values
    assert TOKEN in redis.deleted


def test_resend_otp_commit_failure_rolls_back_and_deletes_token() -> None:
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeInstructorSession(instructor)
    session.commit_error = RuntimeError("database unavailable")
    redis = FakeRedis()
    seed_challenge(redis, instructor.instructor_id)

    with instructor_client(session, redis) as client:
        response = client.post(RESEND_PATH, json={"token": TOKEN})

    assert response.status_code == 500
    assert session.rollbacks == 1
    assert TOKEN not in redis.values
    assert TOKEN in redis.deleted


def test_resend_otp_rejects_extra_fields() -> None:
    instructor = make_instructor(is_2fa_enabled=True)
    session = FakeInstructorSession(instructor)
    redis = FakeRedis()
    seed_challenge(redis, instructor.instructor_id)

    with instructor_client(session, redis) as client:
        response = client.post(
            RESEND_PATH,
            json={"token": TOKEN, "code": NEW_CODE},
        )

    assert response.status_code == 422
    assert session.added == []
    assert session.commits == 0
