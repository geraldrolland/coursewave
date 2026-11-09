"""Student registration and authentication routes."""

import json
import logging
import secrets
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.base_config import Settings
from app.base_dependencies import get_session
from app.security import (
    build_refresh_cookie,
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.services.notification.model import (
    Notification,
    NotificationAudience,
    NotificationType,
)
from app.services.outbox.model import Outbox, OutboxStatus
from app.services.student.google import (
    GoogleUserInfo,
    exchange_code_for_access_token,
    fetch_google_user_info,
)
from app.services.student.model import AuthMethod, Student
from app.services.student.schema import (
    StudentEmailRequest,
    StudentLoginRequest,
    StudentLoginResponse,
    StudentMeResponse,
    StudentMessageResponse,
    StudentPasswordResetRequest,
    StudentRegisterRequest,
    StudentRegisterResponse,
    StudentVerifyEmailRequest,
)

router = APIRouter(prefix="/student/api/v1", tags=["students"])
logger = logging.getLogger("student_service.router")

VERIFICATION_TOKEN_TTL_SECONDS = 120
PASSWORD_RESET_TOKEN_TTL_SECONDS = 120

INVALID_CREDENTIALS = "Invalid email or password"
EMAIL_NOT_VERIFIED = (
    "Email is not verified. A verification email has been sent."
)
INVALID_VERIFICATION_TOKEN = "Invalid or expired verification token"
INVALID_PASSWORD_RESET_TOKEN = "Invalid or expired password reset token"
STUDENT_NOT_FOUND = "Student not found"
EMAIL_VERIFICATION_UNAVAILABLE = "Email verification service unavailable"
AUTH_UNAVAILABLE = "Authentication service unavailable"
PASSWORD_RESET_UNAVAILABLE = "Password reset service unavailable"
PASSWORD_RESET_SENT = (
    "If the email is registered, a password reset link has been sent."
)
INVALID_AUTH_SESSION = "Invalid authentication session"
GOOGLE_AUTH_FAILED_PATH = "/login?msg=google_authentication_failed"
GOOGLE_AUTH_SUCCESS_PATH = "/dashboard"


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
            "userId": str(student.user_id),
            "role": "student",
            "email": student.email,
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


def _google_failure_redirect(settings: Settings) -> RedirectResponse:
    """Redirect the browser to the login page after a failed Google login."""

    return RedirectResponse(
        url=_build_app_url(settings.app_host, GOOGLE_AUTH_FAILED_PATH),
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
    """Register a student and queue an email verification notification."""

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
        auth_method=AuthMethod.EMAIL,
    )
    notification, outbox = _build_email_notification(
        student,
        "email_verification",
        verification_link,
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

            session.add(notification)
            session.add(outbox)
    except IntegrityError as exc:
        if token_stored:
            await _delete_token(redis, token, "verification")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        ) from exc
    except RedisError as exc:
        if token_stored:
            await _delete_token(redis, token, "verification")
        logger.exception("Redis failed while storing a verification token")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=EMAIL_VERIFICATION_UNAVAILABLE,
        ) from exc
    except Exception:
        if token_stored:
            await _delete_token(redis, token, "verification")
        raise

    return StudentRegisterResponse(
        user_id=student.user_id,
        message="Registration successful. Please verify your email.",
    )


@router.post(
    "/login",
    response_model=StudentLoginResponse,
    status_code=status.HTTP_200_OK,
)
async def login_student(
    payload: StudentLoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> StudentLoginResponse:
    """Authenticate a student and issue access and refresh tokens."""

    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis

    student = await _get_student_by_email(session, str(payload.email))
    if student is None or student.password_hash is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
        )

    try:
        password_matches = verify_password(
            payload.password.get_secret_value(),
            student.password_hash,
        )
    except Exception:
        logger.exception("Stored password hash could not be verified")
        password_matches = False

    if not password_matches:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
        )

    if not student.is_email_verified:
        await _store_verification_notification(
            session,
            redis,
            student,
            settings,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=EMAIL_NOT_VERIFIED,
        )

    try:
        access_token, refresh_token = await _create_login_tokens(
            redis,
            student,
            settings,
        )
    except RedisError as exc:
        logger.exception("Redis failed while creating a login session")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=AUTH_UNAVAILABLE,
        ) from exc

    response.headers.append(
        "set-cookie",
        build_refresh_cookie(refresh_token, settings),
    )
    return StudentLoginResponse(access_token=access_token)


@router.get(
    "/google",
    response_model=None,
    status_code=status.HTTP_302_FOUND,
)
async def google_auth(
    request: Request,
    code: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Authenticate a student using a Google OAuth authorization code."""

    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis

    if not code or not code.strip():
        return _google_failure_redirect(settings)

    try:
        access_token = await exchange_code_for_access_token(
            code.strip(),
            settings,
        )
        user_info = await fetch_google_user_info(access_token)
        email = str(user_info.email)
        student = await _get_student_by_email(session, email)

        if student is None:
            first_name, last_name = _google_student_name(user_info)
            student = Student(
                first_name=first_name,
                last_name=last_name,
                email=email,
                is_email_verified=True,
                auth_method=AuthMethod.GOOGLE,
            )
            session.add(student)
            await session.commit()
        elif not student.is_email_verified:
            student.is_email_verified = True
            await session.commit()

        _, refresh_token = await _create_login_tokens(
            redis,
            student,
            settings,
        )
    except Exception:
        logger.exception("Google authentication failed")
        try:
            await session.rollback()
        except Exception:
            logger.exception(
                "Failed to roll back the Google authentication session"
            )
        return _google_failure_redirect(settings)

    response = RedirectResponse(
        url=_build_app_url(settings.app_host, GOOGLE_AUTH_SUCCESS_PATH),
        status_code=status.HTTP_302_FOUND,
    )
    response.headers.append(
        "set-cookie",
        build_refresh_cookie(refresh_token, settings),
    )
    return response


@router.get(
    "/me",
    response_model=StudentMeResponse,
    status_code=status.HTTP_200_OK,
)
async def get_current_student(request: Request) -> StudentMeResponse:
    """Return the authenticated student's session identity."""

    user = getattr(request.state, "user", None)
    if not isinstance(user, dict):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_AUTH_SESSION,
        )

    user_id = user.get("userId")
    email = user.get("email")
    if (
        not isinstance(user_id, str)
        or not user_id
        or not isinstance(email, str)
        or not email
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_AUTH_SESSION,
        )

    return StudentMeResponse(id=user_id, email=email)


@router.patch(
    "/verify-email",
    response_model=StudentMessageResponse,
    status_code=status.HTTP_200_OK,
)
async def verify_email(
    payload: StudentVerifyEmailRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> StudentMessageResponse:
    """Verify a student's email address using a Redis action token."""

    redis: Redis = request.app.state.redis

    try:
        user_id = await _get_token_user_id(redis, payload.token)
    except RedisError as exc:
        logger.exception("Redis failed while loading a verification token")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=EMAIL_VERIFICATION_UNAVAILABLE,
        ) from exc

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_VERIFICATION_TOKEN,
        )

    try:
        student_id = UUID(user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_VERIFICATION_TOKEN,
        ) from exc

    student = await session.get(Student, student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=STUDENT_NOT_FOUND,
        )

    if not student.is_email_verified:
        student.is_email_verified = True
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    await _delete_token(redis, payload.token, "verification")
    return StudentMessageResponse(message="Email verified successfully")


@router.post(
    "/email",
    response_model=StudentMessageResponse,
    status_code=status.HTTP_200_OK,
)
async def send_password_reset_email(
    payload: StudentEmailRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> StudentMessageResponse:
    """Send a password-reset email when the address is registered."""

    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis

    student = await _get_student_by_email(session, str(payload.email))
    if student is None:
        return StudentMessageResponse(message=PASSWORD_RESET_SENT)

    token = secrets.token_urlsafe(32)
    link = build_password_reset_link(settings.app_host, token)
    notification, outbox = _build_email_notification(
        student,
        "forgot_password",
        link,
    )
    token_stored = False

    try:
        await redis.set(
            token,
            str(student.user_id),
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

    return StudentMessageResponse(message=PASSWORD_RESET_SENT)


@router.patch(
    "/password-reset",
    response_model=StudentMessageResponse,
    status_code=status.HTTP_200_OK,
)
async def reset_password(
    payload: StudentPasswordResetRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> StudentMessageResponse:
    """Reset a student's password using a Redis action token."""

    redis: Redis = request.app.state.redis

    try:
        user_id = await _get_token_user_id(redis, payload.token)
    except RedisError as exc:
        logger.exception("Redis failed while loading a password reset token")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=PASSWORD_RESET_UNAVAILABLE,
        ) from exc

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_PASSWORD_RESET_TOKEN,
        )

    try:
        student_id = UUID(user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_PASSWORD_RESET_TOKEN,
        ) from exc

    student = await session.get(Student, student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=STUDENT_NOT_FOUND,
        )

    student.password_hash = hash_password(
        payload.password.get_secret_value()
    )
    if not student.is_email_verified:
        student.is_email_verified = True

    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    await _delete_token(redis, payload.token, "password reset")
    return StudentMessageResponse(message="Password reset successfully")
