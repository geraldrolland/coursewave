"""Student registration and authentication routes."""

import logging
import secrets
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from minio import Minio
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.base_config import Settings
from app.base_dependencies import get_session
from app.security import (
    build_clear_refresh_cookie,
    build_refresh_cookie,
    decode_token,
    hash,
    verify_hash,
)
from app.services.student.model import AuthMethod, Student
from app.services.student.schema import (
    ProfilePictureMetadata,
    StudentEmailRequest,
    StudentLoginRequest,
    StudentLoginResponse,
    StudentMeResponse,
    StudentMessageResponse,
    StudentPasswordResetRequest,
    StudentProfileGetResponse,
    StudentProfileResponse,
    StudentProfileUpdateRequest,
    StudentRegisterRequest,
    StudentRegisterResponse,
    StudentVerifyEmailRequest,
)
from app.services.student.utils import (
    EMAIL_VERIFICATION_UNAVAILABLE,
    INVALID_AUTH_SESSION,
    PASSWORD_RESET_TOKEN_TTL_SECONDS,
    VERIFICATION_TOKEN_TTL_SECONDS,
    _authenticated_user_id,
    _build_app_url,
    _build_email_notification,
    _create_login_tokens,
    _delete_token,
    _get_student_by_email,
    _get_token_user_id,
    _google_failure_redirect,
    _google_student_name,
    _read_profile_picture,
    _store_verification_notification,
    build_password_reset_link,
    build_verification_link,
    exchange_code_for_access_token,
    fetch_google_user_info,
)
from app.services.storage.minio import (
    build_profile_picture_object_key,
    build_profile_picture_url,
    delete_profile_picture,
    object_key_from_profile_picture_url,
    upload_profile_picture,
)

router = APIRouter(prefix="/student/api/v1", tags=["students"])
logger = logging.getLogger("student_service.router")

INVALID_CREDENTIALS = "Invalid email or password"
EMAIL_NOT_VERIFIED = (
    "Email is not verified. A verification email has been sent."
)
INVALID_VERIFICATION_TOKEN = "Invalid or expired verification token"
INVALID_PASSWORD_RESET_TOKEN = "Invalid or expired password reset token"
STUDENT_NOT_FOUND = "Student not found"
AUTH_UNAVAILABLE = "Authentication service unavailable"
PASSWORD_RESET_UNAVAILABLE = "Password reset service unavailable"
PASSWORD_RESET_SENT = (
    "If the email is registered, a password reset link has been sent."
)
LOGOUT_UNAVAILABLE = "Logout service unavailable"
PROFILE_PICTURE_UNAVAILABLE = "Profile picture service unavailable"
GOOGLE_AUTH_SUCCESS_PATH = "/dashboard"


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
        password_hash=hash(payload.password.get_secret_value()),
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
        password_matches = verify_hash(
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
    state: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Authenticate a student using a Google OAuth authorization code."""

    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis

    if not code or not code.strip():
        return _google_failure_redirect(settings, state)

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
        return _google_failure_redirect(settings, state)

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

    user_id = user.get("id")
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


@router.post(
    "/logout",
    response_model=StudentMessageResponse,
    status_code=status.HTTP_200_OK,
)
async def logout(
    request: Request,
    response: Response,
) -> StudentMessageResponse:
    """Delete the authenticated session and clear the refresh cookie."""

    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis

    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_AUTH_SESSION,
        )

    token = authorization[7:].strip()
    try:
        payload = decode_token(token, settings)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_AUTH_SESSION,
        ) from exc

    session_id = payload.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_AUTH_SESSION,
        )

    try:
        await redis.delete(f"session:{session_id}")
    except RedisError as exc:
        logger.exception("Redis failed while deleting a logout session")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=LOGOUT_UNAVAILABLE,
        ) from exc

    response.headers.append(
        "set-cookie",
        build_clear_refresh_cookie(),
    )
    return StudentMessageResponse(message="Logged out successfully")


@router.get(
    "/profile",
    response_model=StudentProfileGetResponse,
    status_code=status.HTTP_200_OK,
)
async def get_student_profile(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> StudentProfileGetResponse:
    """Return the authenticated student's profile."""

    user_id = _authenticated_user_id(request)
    try:
        student_id = UUID(user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_AUTH_SESSION,
        ) from exc

    student = await session.get(Student, student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=STUDENT_NOT_FOUND,
        )

    return StudentProfileGetResponse(
        first_name=student.first_name,
        last_name=student.last_name,
        email=student.email,
        profile_pic_url=student.profile_pic_url,
    )


@router.patch(
    "/profile",
    response_model=StudentProfileResponse,
    status_code=status.HTTP_200_OK,
)
async def update_student_profile(
    request: Request,
    first_name: str | None = Form(default=None),
    middlename: str | None = Form(default=None),
    profile_pic: UploadFile | None = File(default=None),
    session: AsyncSession = Depends(get_session),
) -> StudentProfileResponse:
    """Update a student's profile and optionally replace the profile picture."""

    settings: Settings = request.app.state.settings
    minio_client: Minio = request.app.state.minio

    try:
        profile_update = StudentProfileUpdateRequest(
            first_name=first_name,
            middlename=middlename,
            has_profile_pic=profile_pic is not None,
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc

    user_id = _authenticated_user_id(request)
    try:
        student_id = UUID(user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_AUTH_SESSION,
        ) from exc

    student = await session.get(Student, student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=STUDENT_NOT_FOUND,
        )

    upload_data: bytes | None = None
    picture_metadata: ProfilePictureMetadata | None = None
    if profile_pic is not None:
        upload_data, picture_metadata = await _read_profile_picture(profile_pic)

    generated_object_key: str | None = None

    if picture_metadata is not None:
        object_key: str | None = None
        if student.profile_pic_url:
            object_key = object_key_from_profile_picture_url(
                student.profile_pic_url,
                settings.minio_bucket,
            )
            if object_key is None:
                logger.warning(
                    "Could not parse the existing profile picture URL: %s",
                    student.profile_pic_url,
                )

        if object_key is None:
            generated_object_key = build_profile_picture_object_key(
                picture_metadata.extension
            )
            object_key = generated_object_key

        try:
            await run_in_threadpool(
                upload_profile_picture,
                minio_client,
                settings.minio_bucket,
                object_key,
                upload_data,
                picture_metadata.upload_content_type,
            )
        except Exception as exc:
            logger.exception("Failed to upload a profile picture")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=PROFILE_PICTURE_UNAVAILABLE,
            ) from exc

        if generated_object_key is not None:
            student.profile_pic_url = build_profile_picture_url(
                settings,
                generated_object_key,
            )

    if profile_update.first_name is not None:
        student.first_name = profile_update.first_name
    if profile_update.middlename is not None:
        student.middle_name = profile_update.middlename

    try:
        await session.commit()
    except Exception:
        try:
            await session.rollback()
        except Exception:
            logger.exception("Failed to roll back the profile update")
        if generated_object_key is not None:
            try:
                await run_in_threadpool(
                    delete_profile_picture,
                    minio_client,
                    settings.minio_bucket,
                    generated_object_key,
                )
            except Exception:
                logger.warning(
                    "Failed to delete an orphaned profile picture",
                    exc_info=True,
                )
        raise

    return StudentProfileResponse(
        user_id=student.user_id,
        first_name=student.first_name,
        middle_name=student.middle_name,
        last_name=student.last_name,
        email=student.email,
        profile_pic_url=student.profile_pic_url,
    )


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

    student.password_hash = hash(
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
