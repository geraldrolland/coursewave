"""Utility helpers for the instructor service."""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from fastapi import HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from minio import Minio
from pydantic import SecretStr, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.base_config import Settings
from app.security import (
    create_access_token,
    create_refresh_token,
    hash,
)
from app.services.instructor.model import Instructor
from app.services.notification.model import (
    Notification,
    NotificationAudience,
    NotificationType,
)
from app.services.outbox.model import Outbox, OutboxStatus
from app.services.storage.minio import delete_profile_picture

if TYPE_CHECKING:
    from app.services.instructor.schema import (
        InstructorProfilePictureMetadata,
    )

logger = logging.getLogger("instructor_service.utils")

PASSWORD_PATTERN = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9\s]).{8,}$"
)

VERIFICATION_TOKEN_TTL_SECONDS = 120
OTP_TOKEN_TTL_SECONDS = 86400
OTP_CODE_TTL_SECONDS = 120
OTP_CODE_DIGITS = 6
PASSWORD_RESET_TOKEN_TTL_SECONDS = 300

AUTH_UNAVAILABLE = "Authentication service unavailable"
EMAIL_VERIFICATION_UNAVAILABLE = "Email verification service unavailable"
INVALID_AUTH_SESSION = "Invalid authentication session"
INVALID_VERIFICATION_TOKEN = "Invalid or expired verification token"
INVALID_PASSWORD_RESET_TOKEN = "Invalid or expired password reset token"
INVALID_OTP_TOKEN = "Invalid or expired OTP token"
INVALID_OTP_CODE = "Invalid OTP code"
OTP_SESSION_EXPIRED = "otp verification session expired"
OTP_NOT_EXPIRED = "OTP has not expired"
OTP_RESENT = "OTP resent successfully"
INSTRUCTOR_NOT_FOUND = "Instructor not found"
PASSWORD_RESET_UNAVAILABLE = "Password reset service unavailable"
PASSWORD_RESET_SENT = (
    "If the email is registered, a password reset link has been sent."
)
LOGOUT_UNAVAILABLE = "Logout service unavailable"
GOOGLE_AUTH_FAILED_MSG = "google_authentication_failed"
GOOGLE_AUTH_SUCCESS_PATH = "/dashboard"


def _normalize_email(value: str) -> str:
    """Normalize an email address for storage and lookup."""

    return value.strip().lower()


def _normalize_token(value: str) -> str:
    """Trim an action token and reject empty values."""

    value = value.strip()
    if not value:
        raise ValueError("Token cannot be empty")
    return value


def validate_password_strength(value: SecretStr) -> SecretStr:
    """Validate a password against the instructor account policy."""

    password = value.get_secret_value()

    if len(password) > 128:
        raise ValueError("Password must be at most 128 characters")

    if not PASSWORD_PATTERN.fullmatch(password):
        raise ValueError(
            "Password must be at least 8 characters and contain "
            "an uppercase letter, a lowercase letter, a number, "
            "and a special character"
        )

    return value


def _build_app_url(app_host: str, path: str) -> str:
    """Build an absolute application URL."""

    base_url = app_host.rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = f"http://{base_url}"

    return f"{base_url}{path}"


def _build_app_link(app_host: str, path: str, token: str) -> str:
    """Build an absolute application link containing an action token."""

    return f"{_build_app_url(app_host, path)}?token={token}"


def build_verification_link(app_host: str, token: str) -> str:
    """Build the email verification link for a verification token."""

    return _build_app_link(app_host, "/email-verification", token)


def build_otp_verification_link(app_host: str, token: str) -> str:
    """Build the frontend link for an OTP verification challenge."""

    return _build_app_link(app_host, "/otp-verification", token)


def build_password_reset_link(app_host: str, token: str) -> str:
    """Build the password reset link for a reset token."""

    return _build_app_link(app_host, "/forgot-password", token)


async def _read_profile_picture(
    upload: UploadFile,
) -> tuple[bytes, InstructorProfilePictureMetadata]:
    """Read and validate an uploaded instructor profile picture."""

    from app.services.instructor.schema import (
        InstructorProfilePictureMetadata,
    )

    filename = upload.filename or ""

    if upload.size is not None:
        try:
            InstructorProfilePictureMetadata(
                filename=filename,
                size=upload.size,
                content_type=upload.content_type,
            )
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc

    data = await upload.read()
    try:
        metadata = InstructorProfilePictureMetadata(
            filename=filename,
            size=len(data),
            content_type=upload.content_type,
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc

    return data, metadata


def _authenticated_instructor_id(request: Request) -> UUID:
    """Return the authenticated instructor UUID from the session."""

    user = getattr(request.state, "user", None)
    if not isinstance(user, dict):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_AUTH_SESSION,
        )

    instructor_id = user.get("id")
    if not isinstance(instructor_id, str) or not instructor_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_AUTH_SESSION,
        )

    try:
        return UUID(instructor_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_AUTH_SESSION,
        ) from exc


async def _delete_uploaded_picture(
    minio_client: Minio,
    bucket: str,
    object_key: str,
) -> None:
    """Delete a newly uploaded object without masking the original failure."""

    try:
        await run_in_threadpool(
            delete_profile_picture,
            minio_client,
            bucket,
            object_key,
        )
    except Exception:
        logger.warning(
            "Failed to delete an orphaned instructor profile picture",
            exc_info=True,
        )


def _google_failure_redirect(
    settings: Settings,
    state: str,
) -> RedirectResponse:
    """Redirect the browser to the state path after a failed Google login."""

    path = f"/{state.strip().lstrip('/')}"
    base_url = _build_app_url(settings.app_host, path)
    url = f"{base_url}?msg={GOOGLE_AUTH_FAILED_MSG}"
    return RedirectResponse(
        url=url,
        status_code=status.HTTP_302_FOUND,
    )


def _build_email_notification(
    instructor: Instructor,
    template_type: str,
    content: dict[str, Any],
) -> tuple[Notification, Outbox]:
    """Build the email notification and outbox row for an instructor action."""

    notification = Notification(
        type=NotificationType.EMAIL,
        payload={
            "template_type": template_type,
            "content": content,
            "to_email": instructor.email,
        },
        belongs_to=NotificationAudience.INSTRUCTOR,
        user_id=instructor.instructor_id,
    )
    outbox = Outbox(
        status=OutboxStatus.PENDING,
        payload={
            "argument": {
                "notification_id": str(notification.notification_id),
            },
            "task_name": "process_notification",
        },
        table="notification",
        ref_id=str(notification.notification_id),
    )
    return notification, outbox


async def _delete_token(redis: Redis, token: str, purpose: str) -> None:
    """Best-effort cleanup for an action token."""

    try:
        await redis.delete(token)
    except RedisError:
        logger.warning("Failed to delete %s token", purpose)


async def _get_instructor_by_email(
    session: AsyncSession,
    email: str,
) -> Instructor | None:
    """Return the instructor with the given email, if one exists."""

    result = await session.execute(
        select(Instructor).where(Instructor.email == email)
    )
    return result.scalar_one_or_none()


async def _get_token_instructor_id(redis: Redis, token: str) -> str | None:
    """Return the instructor ID stored for an action token."""

    value = await redis.get(token)
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    return None


async def _get_otp_challenge(
    redis: Redis,
    token: str,
) -> dict[str, str | int] | None:
    """Return a validated OTP challenge for a token."""

    value = await redis.get(token)
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if not isinstance(value, str):
        return None

    try:
        challenge = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(challenge, dict):
        return None

    instructor_id = challenge.get("instructor_id")
    code_hash = challenge.get("code_hash")
    expires_at = challenge.get("exp")
    if not isinstance(instructor_id, str) or not instructor_id:
        return None
    if not isinstance(code_hash, str) or not code_hash:
        return None
    if isinstance(expires_at, bool) or not isinstance(
        expires_at,
        (int, float),
    ):
        return None

    return {
        "instructor_id": instructor_id,
        "code_hash": code_hash,
        "exp": int(expires_at),
    }


def _is_otp_expired(challenge: dict[str, str | int]) -> bool:
    """Return whether an OTP challenge is past its two-minute expiry."""

    return int(challenge["exp"]) < int(time.time())


async def _create_login_tokens(
    redis: Redis,
    instructor: Instructor,
    settings: Settings,
) -> tuple[str, str]:
    """Create a Redis session and the matching access and refresh tokens."""

    session_id = str(uuid4())
    access_token = create_access_token(session_id, settings)
    refresh_token = create_refresh_token(session_id, settings)
    session_data = json.dumps(
        {
            "id": str(instructor.instructor_id),
            "email": instructor.email,
            "role": instructor.role,
        }
    )

    await redis.set(
        f"session:{session_id}",
        session_data,
        ex=settings.refresh_token_max_age_seconds,
    )
    return access_token, refresh_token


async def _store_verification_notification(
    session: AsyncSession,
    redis: Redis,
    instructor: Instructor,
    settings: Settings,
) -> None:
    """Create a new verification token, notification, and outbox row."""

    token = secrets.token_urlsafe(32)
    link = build_verification_link(settings.app_host, token)
    notification, outbox = _build_email_notification(
        instructor,
        "email_verification",
        {"link": link},
    )
    token_stored = False

    try:
        await redis.set(
            token,
            str(instructor.instructor_id),
            ex=VERIFICATION_TOKEN_TTL_SECONDS,
        )
        token_stored = True
        session.add(notification)
        session.add(outbox)
        await session.commit()
    except RedisError as exc:
        if token_stored:
            await _delete_token(redis, token, "verification")
        await session.rollback()
        logger.exception("Redis failed while storing a verification token")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=EMAIL_VERIFICATION_UNAVAILABLE,
        ) from exc
    except Exception:
        if token_stored:
            await _delete_token(redis, token, "verification")
        await session.rollback()
        raise


async def _store_password_reset_notification(
    session: AsyncSession,
    redis: Redis,
    instructor: Instructor,
    settings: Settings,
) -> None:
    """Create a password-reset token, notification, and outbox row."""

    token = secrets.token_urlsafe(32)
    link = build_password_reset_link(settings.app_host, token)
    notification, outbox = _build_email_notification(
        instructor,
        "forgot_password",
        {"link": link},
    )
    token_stored = False

    try:
        await redis.set(
            token,
            str(instructor.instructor_id),
            ex=PASSWORD_RESET_TOKEN_TTL_SECONDS,
        )
        token_stored = True
        session.add(notification)
        session.add(outbox)
        await session.commit()
    except RedisError as exc:
        if token_stored:
            await _delete_token(redis, token, "password reset")
        await session.rollback()
        logger.exception("Redis failed while storing a password reset token")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=PASSWORD_RESET_UNAVAILABLE,
        ) from exc
    except Exception:
        if token_stored:
            await _delete_token(redis, token, "password reset")
        await session.rollback()
        raise


def _generate_otp_code() -> str:
    """Generate a zero-padded six-digit one-time password."""

    return f"{secrets.randbelow(10 ** OTP_CODE_DIGITS):0{OTP_CODE_DIGITS}d}"


def _build_otp_challenge(
    instructor_id: object,
    otp_code: str,
) -> dict[str, str | int]:
    """Build the Redis payload for a hashed OTP challenge."""

    return {
        "instructor_id": str(instructor_id),
        "code_hash": hash(otp_code),
        "exp": int(time.time()) + OTP_CODE_TTL_SECONDS,
    }


async def _store_otp_challenge(
    session: AsyncSession,
    redis: Redis,
    instructor: Instructor,
    token: str,
    otp_code: str,
) -> None:
    """Store an OTP challenge and queue its email notification and outbox row."""

    notification, outbox = _build_email_notification(
        instructor,
        "otp",
        {"otp_code": otp_code},
    )
    challenge = json.dumps(
        _build_otp_challenge(instructor.instructor_id, otp_code)
    )

    try:
        await redis.set(
            token,
            challenge,
            ex=OTP_TOKEN_TTL_SECONDS,
        )
        session.add(notification)
        session.add(outbox)
        await session.commit()
    except RedisError as exc:
        await _delete_token(redis, token, "OTP")
        await session.rollback()
        logger.exception("Redis failed while storing an OTP challenge")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=AUTH_UNAVAILABLE,
        ) from exc
    except Exception:
        await _delete_token(redis, token, "OTP")
        await session.rollback()
        raise


async def _store_otp_notification(
    session: AsyncSession,
    redis: Redis,
    instructor: Instructor,
    settings: Settings,
) -> str:
    """Create an OTP challenge and queue its email notification."""

    otp_code = _generate_otp_code()
    token = secrets.token_urlsafe(32)
    await _store_otp_challenge(
        session,
        redis,
        instructor,
        token,
        otp_code,
    )
    return build_otp_verification_link(settings.app_host, token)


__all__ = [
    "AUTH_UNAVAILABLE",
    "EMAIL_VERIFICATION_UNAVAILABLE",
    "GOOGLE_AUTH_FAILED_MSG",
    "GOOGLE_AUTH_SUCCESS_PATH",
    "INSTRUCTOR_NOT_FOUND",
    "INVALID_AUTH_SESSION",
    "INVALID_OTP_CODE",
    "INVALID_OTP_TOKEN",
    "INVALID_PASSWORD_RESET_TOKEN",
    "INVALID_VERIFICATION_TOKEN",
    "LOGOUT_UNAVAILABLE",
    "OTP_CODE_DIGITS",
    "OTP_CODE_TTL_SECONDS",
    "OTP_NOT_EXPIRED",
    "OTP_RESENT",
    "OTP_SESSION_EXPIRED",
    "OTP_TOKEN_TTL_SECONDS",
    "PASSWORD_PATTERN",
    "PASSWORD_RESET_SENT",
    "PASSWORD_RESET_TOKEN_TTL_SECONDS",
    "PASSWORD_RESET_UNAVAILABLE",
    "VERIFICATION_TOKEN_TTL_SECONDS",
    "_authenticated_instructor_id",
    "_build_app_url",
    "_build_email_notification",
    "_build_otp_challenge",
    "_create_login_tokens",
    "_delete_token",
    "_delete_uploaded_picture",
    "_generate_otp_code",
    "_get_instructor_by_email",
    "_get_otp_challenge",
    "_get_token_instructor_id",
    "_google_failure_redirect",
    "_is_otp_expired",
    "_normalize_email",
    "_normalize_token",
    "_read_profile_picture",
    "_store_otp_challenge",
    "_store_otp_notification",
    "_store_password_reset_notification",
    "_store_verification_notification",
    "build_otp_verification_link",
    "build_password_reset_link",
    "build_verification_link",
    "validate_password_strength",
]
