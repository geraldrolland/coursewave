"""Student registration routes."""

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.base_config import Settings
from app.base_dependencies import get_session
from app.security import hash_password
from app.services.student.model import Outbox, OutboxStatus, Student
from app.services.student.schema import (
    StudentRegisterRequest,
    StudentRegisterResponse,
)

router = APIRouter(prefix="/student/api/v1", tags=["students"])
logger = logging.getLogger("student_service.router")

VERIFICATION_TOKEN_TTL_SECONDS = 120
OUTBOX_TOPIC = "student.registered"


def build_verification_link(app_host: str, token: str) -> str:
    """Build the email verification link for a registration token."""

    base_url = app_host.rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = f"http://{base_url}"

    return f"{base_url}/email-verification?token={token}"


async def _delete_verification_token(redis: Redis, token: str) -> None:
    """Best-effort cleanup for a token that will not be committed."""

    try:
        await redis.delete(token)
    except RedisError:
        logger.warning(
            "Failed to delete verification token after registration failure"
        )


@router.post(
    "/register",
    response_model=StudentRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_student(
    payload: StudentRegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> StudentRegisterResponse:
    """Register a student and enqueue the verification event."""

    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis

    email = str(payload.email)
    token = secrets.token_urlsafe(32)
    verification_link = build_verification_link(settings.app_host, token)
    token_stored = False

    student = Student(
        first_name=payload.first_name,
        middle_name=payload.middle_name,
        last_name=payload.last_name,
        password_hash=hash_password(payload.password.get_secret_value()),
        email=email,
        is_email_verified=False,
    )
    outbox = Outbox(
        status=OutboxStatus.pending,
        topic=OUTBOX_TOPIC,
        payload={
            "recipient_email": email,
            "verification_link": verification_link,
        },
    )

    try:
        async with session.begin():
            session.add(student)
            await session.flush()

            await redis.set(
                token,
                str(student.user_id),
                ex=VERIFICATION_TOKEN_TTL_SECONDS,
            )
            token_stored = True

            session.add(outbox)
    except IntegrityError as exc:
        if token_stored:
            await _delete_verification_token(redis, token)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        ) from exc
    except RedisError as exc:
        if token_stored:
            await _delete_verification_token(redis, token)
        logger.exception("Redis failed while storing a verification token")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email verification service unavailable",
        ) from exc
    except Exception:
        if token_stored:
            await _delete_verification_token(redis, token)
        raise

    return StudentRegisterResponse(
        user_id=student.user_id,
        message="Registration successful. Please verify your email.",
    )
