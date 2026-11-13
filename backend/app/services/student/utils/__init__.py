"""Utility helpers for the student service."""

from __future__ import annotations

import json
import logging
import re
import secrets
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from pydantic import SecretStr, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.base_config import Settings
from app.security import create_access_token, create_refresh_token
from app.services.notification.model import (
    Notification,
    NotificationAudience,
    NotificationType,
)
from app.services.outbox.model import Outbox, OutboxStatus
from app.services.student.google import (
    GOOGLE_HTTP_TIMEOUT_SECONDS,
    GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL,
    GoogleAuthError,
    GoogleUserInfo,
)
from app.services.student.model import Student

if TYPE_CHECKING:
    from app.services.student.schema import ProfilePictureMetadata

logger = logging.getLogger("student_service.utils")

PASSWORD_PATTERN = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9\s]).{8,}$"
)

VERIFICATION_TOKEN_TTL_SECONDS = 120
PASSWORD_RESET_TOKEN_TTL_SECONDS = 300

EMAIL_VERIFICATION_UNAVAILABLE = "Email verification service unavailable"
INVALID_AUTH_SESSION = "Invalid authentication session"
GOOGLE_AUTH_FAILED_MSG = "google_authentication_failed"


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


def build_password_reset_link(app_host: str, token: str) -> str:
    """Build the password reset link for a reset token."""

    return _build_app_link(app_host, "/forgot-password", token)


def _build_email_notification(
    student: Student,
    template_type: str,
    link: str,
) -> tuple[Notification, Outbox]:
    """Build the email notification and outbox row for a student action."""

    notification = Notification(
        type=NotificationType.EMAIL,
        payload={
            "template_type": template_type,
            "content": {"link": link},
            "to_email": student.email,
        },
        belongs_to=NotificationAudience.STUDENT,
        user_id=student.user_id,
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


def _authenticated_user_id(request: Request) -> str:
    """Return the authenticated user ID from the middleware session."""

    user = getattr(request.state, "user", None)
    if not isinstance(user, dict):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_AUTH_SESSION,
        )

    user_id = user.get("id")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_AUTH_SESSION,
        )

    return user_id


def _validate_profile_picture_metadata(
    filename: str,
    size: int,
    content_type: str | None,
) -> ProfilePictureMetadata:
    """Validate uploaded profile-picture metadata with Pydantic."""

    from app.services.student.schema import ProfilePictureMetadata

    try:
        return ProfilePictureMetadata(
            filename=filename,
            size=size,
            content_type=content_type,
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


async def _read_profile_picture(
    upload: UploadFile,
) -> tuple[bytes, ProfilePictureMetadata]:
    """Read an upload and validate its filename, size, and content."""

    filename = upload.filename or ""

    if upload.size is not None:
        _validate_profile_picture_metadata(
            filename,
            upload.size,
            upload.content_type,
        )

    data = await upload.read()
    metadata = _validate_profile_picture_metadata(
        filename,
        len(data),
        upload.content_type,
    )
    return data, metadata


async def _get_student_by_email(
    session: AsyncSession,
    email: str,
) -> Student | None:
    """Return the student with the given email, if one exists."""

    result = await session.execute(
        select(Student).where(Student.email == email)
    )
    return result.scalar_one_or_none()


async def _get_token_user_id(redis: Redis, token: str) -> str | None:
    """Return the user ID stored for an action token."""

    value = await redis.get(token)
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    return None


async def _create_login_tokens(
    redis: Redis,
    student: Student,
    settings: Settings,
) -> tuple[str, str]:
    """Create a Redis session and the matching access and refresh tokens."""

    session_id = str(uuid4())
    access_token = create_access_token(session_id, settings)
    refresh_token = create_refresh_token(session_id, settings)
    session_data = json.dumps(
        {
            "id": str(student.user_id),
            "email": student.email,
            "role": student.role,
        }
    )

    await redis.set(
        f"session:{session_id}",
        session_data,
        ex=settings.refresh_token_max_age_seconds,
    )
    return access_token, refresh_token


def _google_student_name(user_info: GoogleUserInfo) -> tuple[str, str]:
    """Derive a first and last name from the Google userinfo payload."""

    first_name = (user_info.given_name or "").strip()
    last_name = (user_info.family_name or "").strip()
    full_name = (user_info.name or "").strip()

    if not first_name or not last_name:
        parts = full_name.split()
        if not first_name and parts:
            first_name = parts[0]
        if not last_name and len(parts) > 1:
            last_name = " ".join(parts[1:])

    if not first_name:
        first_name = str(user_info.email).split("@", 1)[0]

    return first_name[:100], (last_name or "Student")[:100]


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


async def _store_verification_notification(
    session: AsyncSession,
    redis: Redis,
    student: Student,
    settings: Settings,
) -> None:
    """Create a new verification token, notification, and outbox row."""

    token = secrets.token_urlsafe(32)
    link = build_verification_link(settings.app_host, token)
    notification, outbox = _build_email_notification(
        student,
        "email_verification",
        link,
    )
    token_stored = False

    try:
        await redis.set(
            token,
            str(student.user_id),
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


async def exchange_code_for_access_token(
    code: str,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Exchange a Google authorization code for an access token."""

    payload = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": settings.google_redirect_uri,
        "grant_type": "authorization_code",
    }

    try:
        if client is None:
            async with httpx.AsyncClient(
                timeout=GOOGLE_HTTP_TIMEOUT_SECONDS
            ) as http_client:
                response = await http_client.post(
                    GOOGLE_TOKEN_URL,
                    data=payload,
                )
        else:
            response = await client.post(GOOGLE_TOKEN_URL, data=payload)

        response.raise_for_status()
        body: Any = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GoogleAuthError(
            "Google token exchange failed"
        ) from exc

    access_token = (
        body.get("access_token") if isinstance(body, dict) else None
    )
    if not isinstance(access_token, str) or not access_token:
        raise GoogleAuthError(
            "Google token response did not include an access token"
        )

    return access_token


async def fetch_google_user_info(
    access_token: str,
    client: httpx.AsyncClient | None = None,
) -> GoogleUserInfo:
    """Fetch the Google account details for an access token."""

    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        if client is None:
            async with httpx.AsyncClient(
                timeout=GOOGLE_HTTP_TIMEOUT_SECONDS
            ) as http_client:
                response = await http_client.get(
                    GOOGLE_USERINFO_URL,
                    headers=headers,
                )
        else:
            response = await client.get(
                GOOGLE_USERINFO_URL,
                headers=headers,
            )

        response.raise_for_status()
        body: Any = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GoogleAuthError(
            "Google userinfo request failed"
        ) from exc

    try:
        return GoogleUserInfo.model_validate(body)
    except ValidationError as exc:
        raise GoogleAuthError("Google userinfo response is invalid") from exc


def validate_password_strength(value: SecretStr) -> SecretStr:
    """Validate a password against the shared account policy."""

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


def _normalize_email(value: str) -> str:
    """Normalize an email address for storage and lookup."""

    return value.strip().lower()


def _normalize_token(value: str) -> str:
    """Trim an action token and reject empty values."""

    value = value.strip()
    if not value:
        raise ValueError("Token cannot be empty")
    return value
