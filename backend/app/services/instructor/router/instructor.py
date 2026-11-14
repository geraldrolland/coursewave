"""Instructor authentication routes."""

import logging
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from minio import Minio
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.base_config import Settings
from app.base_dependencies import get_session
from app.security import (
    build_clear_refresh_cookie,
    build_refresh_cookie,
    decode_token,
    hash,
    verify_hash,
)
from app.services.instructor.google import (
    exchange_code_for_access_token,
    fetch_google_user_info,
)
from app.services.instructor.model import AuthMethod, Instructor
from app.services.instructor.schema import (
    InstructorCoverImageForm,
    InstructorCoverImageResponse,
    InstructorEmailRequest,
    InstructorLoginRequest,
    InstructorLoginResponse,
    InstructorMeResponse,
    InstructorMessageResponse,
    InstructorPasswordResetRequest,
    InstructorProfileCreateRequest,
    InstructorProfilePicForm,
    InstructorProfilePicResponse,
    InstructorProfileUpdateRequest,
    InstructorRegisterRequest,
    InstructorRegisterResponse,
    InstructorResendOtpRequest,
    InstructorVerifyEmailRequest,
    InstructorVerifyOtpRequest,
)
from app.services.instructor.utils import (
    AUTH_UNAVAILABLE,
    EMAIL_VERIFICATION_UNAVAILABLE,
    GOOGLE_AUTH_SUCCESS_PATH,
    INSTRUCTOR_NOT_FOUND,
    INVALID_AUTH_SESSION,
    INVALID_OTP_CODE,
    INVALID_OTP_TOKEN,
    INVALID_PASSWORD_RESET_TOKEN,
    INVALID_VERIFICATION_TOKEN,
    LOGOUT_UNAVAILABLE,
    OTP_NOT_EXPIRED,
    OTP_RESENT,
    OTP_SESSION_EXPIRED,
    PASSWORD_RESET_SENT,
    PASSWORD_RESET_UNAVAILABLE,
    VERIFICATION_TOKEN_TTL_SECONDS,
    _authenticated_instructor_id,
    _build_app_url,
    _build_email_notification,
    _create_login_tokens,
    _delete_token,
    _delete_uploaded_picture,
    _generate_otp_code,
    _get_instructor_by_email,
    _get_otp_challenge,
    _get_token_instructor_id,
    _google_failure_redirect,
    _is_otp_expired,
    _read_profile_picture,
    _store_otp_challenge,
    _store_otp_notification,
    _store_password_reset_notification,
    _store_verification_notification,
    build_verification_link,
)
from app.services.storage.minio import (
    build_cover_image_url,
    build_profile_picture_object_key,
    build_profile_picture_url,
    delete_profile_picture,
    object_key_from_profile_picture_url,
    upload_profile_picture,
)

router = APIRouter(prefix="/instructor/api/v1", tags=["instructors"])
logger = logging.getLogger("instructor_service.router")

EMAIL_ALREADY_REGISTERED = "Email is already registered"
REGISTRATION_MESSAGE = "Registration successful. Please verify your email."
INVALID_CREDENTIALS = "Invalid email or password"
EMAIL_NOT_VERIFIED = (
    "Email is not verified. A verification email has been sent."
)
PROFILE_PICTURE_UNAVAILABLE = "Profile picture service unavailable"
PROFILE_PICTURE_DELETED = "Profile picture deleted successfully"
PROFILE_PICTURE_NOT_SET = "Profile picture is not set"
COVER_IMAGE_UNAVAILABLE = "Cover image service unavailable"
COVER_IMAGE_DELETED = "Cover image deleted successfully"
COVER_IMAGE_NOT_SET = "Cover image is not set"
PROFILE_ALREADY_EXISTS = "Instructor profile already exists"
PROFILE_NOT_FOUND = "Instructor profile not found"


@router.post(
    "/register",
    response_model=InstructorRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_instructor(
    payload: InstructorRegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> InstructorRegisterResponse:
    """Register an instructor and queue an email verification notification."""

    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis

    email = str(payload.email)
    token = secrets.token_urlsafe(32)
    verification_link = build_verification_link(settings.app_host, token)
    token_stored = False

    instructor = Instructor(
        email=email,
        password_hash=hash(payload.password.get_secret_value()),
        is_email_verified=False,
        auth_method=AuthMethod.EMAIL,
    )
    notification, outbox = _build_email_notification(
        instructor,
        "email_verification",
        {"link": verification_link},
    )

    try:
        async with session.begin():
            session.add(instructor)
            await session.flush()

            await redis.set(
                token,
                str(instructor.instructor_id),
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
            detail=EMAIL_ALREADY_REGISTERED,
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

    return InstructorRegisterResponse(
        instructor_id=instructor.instructor_id,
        message=REGISTRATION_MESSAGE,
    )


@router.post(
    "/login",
    response_model=InstructorLoginResponse,
    responses={
        status.HTTP_302_FOUND: {
            "description": "Two-factor authentication required",
        },
    },
)
async def login_instructor(
    payload: InstructorLoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> InstructorLoginResponse | RedirectResponse:
    """Authenticate an instructor and optionally start a 2FA challenge."""

    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis

    instructor = await _get_instructor_by_email(session, str(payload.email))
    if instructor is None or instructor.password_hash is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
        )

    try:
        password_matches = verify_hash(
            payload.password.get_secret_value(),
            instructor.password_hash,
        )
    except Exception:
        logger.exception("Stored password hash could not be verified")
        password_matches = False

    if not password_matches:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
        )

    if not instructor.is_email_verified:
        await _store_verification_notification(
            session,
            redis,
            instructor,
            settings,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=EMAIL_NOT_VERIFIED,
        )

    if instructor.is_2fa_enabled:
        redirect_url = await _store_otp_notification(
            session,
            redis,
            instructor,
            settings,
        )
        return RedirectResponse(
            url=redirect_url,
            status_code=status.HTTP_302_FOUND,
        )

    try:
        access_token, refresh_token = await _create_login_tokens(
            redis,
            instructor,
            settings,
        )
    except RedisError as exc:
        logger.exception("Redis failed while creating an instructor session")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=AUTH_UNAVAILABLE,
        ) from exc

    response.headers.append(
        "set-cookie",
        build_refresh_cookie(refresh_token, settings),
    )
    return InstructorLoginResponse(access_token=access_token)


@router.post(
    "/verify-otp",
    response_model=InstructorLoginResponse,
    status_code=status.HTTP_200_OK,
)
async def verify_otp(
    payload: InstructorVerifyOtpRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> InstructorLoginResponse:
    """Verify an instructor OTP challenge and issue login tokens."""

    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis

    try:
        challenge = await _get_otp_challenge(redis, payload.token)
    except RedisError as exc:
        logger.exception("Redis failed while loading an OTP challenge")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=AUTH_UNAVAILABLE,
        ) from exc

    if challenge is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_OTP_TOKEN,
        )

    if _is_otp_expired(challenge):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=INVALID_OTP_TOKEN,
        )

    try:
        code_matches = verify_hash(
            payload.code,
            str(challenge["code_hash"]),
        )
    except Exception:
        logger.exception("Stored OTP hash could not be verified")
        code_matches = False

    if not code_matches:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_OTP_CODE,
        )

    try:
        instructor_id = UUID(str(challenge["instructor_id"]))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_OTP_TOKEN,
        ) from exc

    instructor = await session.get(Instructor, instructor_id)
    if instructor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INSTRUCTOR_NOT_FOUND,
        )

    try:
        await redis.delete(payload.token)
    except RedisError as exc:
        logger.exception("Redis failed while deleting an OTP challenge")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=AUTH_UNAVAILABLE,
        ) from exc

    try:
        access_token, refresh_token = await _create_login_tokens(
            redis,
            instructor,
            settings,
        )
    except RedisError as exc:
        logger.exception("Redis failed while creating an instructor session")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=AUTH_UNAVAILABLE,
        ) from exc

    response.headers.append(
        "set-cookie",
        build_refresh_cookie(refresh_token, settings),
    )
    return InstructorLoginResponse(access_token=access_token)


@router.post(
    "/resend-otp",
    response_model=InstructorMessageResponse,
    status_code=status.HTTP_200_OK,
)
async def resend_otp(
    payload: InstructorResendOtpRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> InstructorMessageResponse:
    """Replace an expired OTP challenge and queue a new code."""

    redis: Redis = request.app.state.redis

    try:
        challenge = await _get_otp_challenge(redis, payload.token)
    except RedisError as exc:
        logger.exception("Redis failed while loading an OTP challenge")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=AUTH_UNAVAILABLE,
        ) from exc

    if challenge is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=OTP_SESSION_EXPIRED,
        )

    if not _is_otp_expired(challenge):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=OTP_NOT_EXPIRED,
        )

    try:
        instructor_id = UUID(str(challenge["instructor_id"]))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=OTP_SESSION_EXPIRED,
        ) from exc

    instructor = await session.get(Instructor, instructor_id)
    if instructor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INSTRUCTOR_NOT_FOUND,
        )

    await _store_otp_challenge(
        session,
        redis,
        instructor,
        payload.token,
        _generate_otp_code(),
    )
    return InstructorMessageResponse(message=OTP_RESENT)


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
    """Authenticate an instructor using a Google OAuth authorization code."""

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
        instructor = await _get_instructor_by_email(session, email)

        if instructor is None:
            instructor = Instructor(
                email=email,
                is_email_verified=True,
                auth_method=AuthMethod.GOOGLE,
            )
            session.add(instructor)
            await session.commit()
        elif not instructor.is_email_verified:
            instructor.is_email_verified = True
            await session.commit()

        if instructor.is_2fa_enabled:
            redirect_url = await _store_otp_notification(
                session,
                redis,
                instructor,
                settings,
            )
            return RedirectResponse(
                url=redirect_url,
                status_code=status.HTTP_302_FOUND,
            )

        _, refresh_token = await _create_login_tokens(
            redis,
            instructor,
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
    response_model=InstructorMeResponse,
    status_code=status.HTTP_200_OK,
)
async def get_current_instructor(request: Request) -> InstructorMeResponse:
    """Return the authenticated instructor's session identity."""

    user = getattr(request.state, "user", None)
    if not isinstance(user, dict):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_AUTH_SESSION,
        )

    instructor_id = user.get("id")
    email = user.get("email")
    if (
        not isinstance(instructor_id, str)
        or not instructor_id
        or not isinstance(email, str)
        or not email
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_AUTH_SESSION,
        )

    return InstructorMeResponse(id=instructor_id, email=email)


@router.post(
    "/logout",
    response_model=InstructorMessageResponse,
    status_code=status.HTTP_200_OK,
)
async def logout(
    request: Request,
    response: Response,
) -> InstructorMessageResponse:
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
    return InstructorMessageResponse(message="Logged out successfully")


@router.post(
    "/profile",
    response_model=InstructorProfileCreateRequest,
    response_model_exclude_none=True,
    status_code=status.HTTP_200_OK,
)
async def create_instructor_profile(
    payload: InstructorProfileCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> InstructorProfileCreateRequest:
    """Create the authenticated instructor's JSON profile."""

    instructor_id = _authenticated_instructor_id(request)
    instructor = await session.get(Instructor, instructor_id)
    if instructor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INSTRUCTOR_NOT_FOUND,
        )

    if instructor.profile is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=PROFILE_ALREADY_EXISTS,
        )

    instructor.profile = payload.model_dump(
        mode="json",
        exclude_none=True,
    )
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return payload


@router.get(
    "/profile",
    response_model=InstructorProfileCreateRequest,
    response_model_exclude_none=True,
    status_code=status.HTTP_200_OK,
)
async def get_instructor_profile(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> InstructorProfileCreateRequest:
    """Return the authenticated instructor's JSON profile."""

    instructor_id = _authenticated_instructor_id(request)
    instructor = await session.get(Instructor, instructor_id)
    if instructor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INSTRUCTOR_NOT_FOUND,
        )

    if instructor.profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=PROFILE_NOT_FOUND,
        )

    return InstructorProfileCreateRequest.model_validate(
        instructor.profile
    )


@router.patch(
    "/profile",
    response_model=InstructorProfileCreateRequest,
    response_model_exclude_none=True,
    status_code=status.HTTP_200_OK,
)
async def update_instructor_profile(
    payload: InstructorProfileUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> InstructorProfileCreateRequest:
    """Update the authenticated instructor's JSON profile."""

    instructor_id = _authenticated_instructor_id(request)
    instructor = await session.get(Instructor, instructor_id)
    if instructor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INSTRUCTOR_NOT_FOUND,
        )

    if instructor.profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=PROFILE_NOT_FOUND,
        )

    updates = payload.model_dump(
        mode="json",
        exclude_none=True,
        exclude_unset=True,
    )
    merged_profile = {**instructor.profile, **updates}
    updated_profile = InstructorProfileCreateRequest.model_validate(
        merged_profile
    )

    instructor.profile = updated_profile.model_dump(
        mode="json",
        exclude_none=True,
    )
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return updated_profile


@router.post(
    "/profile-pic",
    response_model=InstructorProfilePicResponse,
    status_code=status.HTTP_200_OK,
)
async def upload_instructor_profile_pic(
    request: Request,
    profile_form: Annotated[InstructorProfilePicForm, Form()],
    session: AsyncSession = Depends(get_session),
) -> InstructorProfilePicResponse:
    """Upload or replace the authenticated instructor's profile picture."""

    settings: Settings = request.app.state.settings
    minio_client: Minio = request.app.state.minio

    upload_data, picture_metadata = await _read_profile_picture(
        profile_form.picture
    )
    instructor_id = _authenticated_instructor_id(request)
    instructor = await session.get(Instructor, instructor_id)
    if instructor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INSTRUCTOR_NOT_FOUND,
        )

    generated_object_key = False
    if instructor.profile_pic_url is None:
        object_key = build_profile_picture_object_key(
            picture_metadata.extension
        )
        instructor.profile_pic_url = build_profile_picture_url(
            settings,
            object_key,
        )
        generated_object_key = True
    else:
        object_key = object_key_from_profile_picture_url(
            instructor.profile_pic_url,
            settings.minio_bucket,
        )
        if object_key is None:
            logger.warning(
                "Could not parse the existing profile picture URL: %s",
                instructor.profile_pic_url,
            )
            object_key = build_profile_picture_object_key(
                picture_metadata.extension
            )
            instructor.profile_pic_url = build_profile_picture_url(
                settings,
                object_key,
            )
            generated_object_key = True

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
        await session.rollback()
        if generated_object_key:
            await _delete_uploaded_picture(
                minio_client,
                settings.minio_bucket,
                object_key,
            )
        logger.exception("Failed to upload an instructor profile picture")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=PROFILE_PICTURE_UNAVAILABLE,
        ) from exc

    try:
        await session.commit()
    except Exception:
        await session.rollback()
        if generated_object_key:
            await _delete_uploaded_picture(
                minio_client,
                settings.minio_bucket,
                object_key,
            )
        raise

    return InstructorProfilePicResponse(
        profile_pic_url=instructor.profile_pic_url,
    )


@router.delete(
    "/profile-pic",
    response_model=InstructorMessageResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_instructor_profile_pic(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> InstructorMessageResponse:
    """Delete the authenticated instructor's profile picture."""

    settings: Settings = request.app.state.settings
    minio_client: Minio = request.app.state.minio

    instructor_id = _authenticated_instructor_id(request)
    instructor = await session.get(Instructor, instructor_id)
    if instructor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INSTRUCTOR_NOT_FOUND,
        )

    if instructor.profile_pic_url is None:
        return InstructorMessageResponse(message=PROFILE_PICTURE_NOT_SET)

    object_key = object_key_from_profile_picture_url(
        instructor.profile_pic_url,
        settings.minio_bucket,
    )
    if object_key is not None:
        try:
            await run_in_threadpool(
                delete_profile_picture,
                minio_client,
                settings.minio_bucket,
                object_key,
            )
        except Exception as exc:
            logger.exception("Failed to delete an instructor profile picture")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=PROFILE_PICTURE_UNAVAILABLE,
            ) from exc
    else:
        logger.warning(
            "Could not parse the existing profile picture URL: %s",
            instructor.profile_pic_url,
        )

    instructor.profile_pic_url = None
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return InstructorMessageResponse(message=PROFILE_PICTURE_DELETED)


@router.post(
    "/cover-image",
    response_model=InstructorCoverImageResponse,
    status_code=status.HTTP_200_OK,
)
async def upload_instructor_cover_image(
    request: Request,
    cover_form: Annotated[InstructorCoverImageForm, Form()],
    session: AsyncSession = Depends(get_session),
) -> InstructorCoverImageResponse:
    """Upload or replace the authenticated instructor's cover image."""

    settings: Settings = request.app.state.settings
    minio_client: Minio = request.app.state.minio

    upload_data, picture_metadata = await _read_profile_picture(
        cover_form.picture
    )
    instructor_id = _authenticated_instructor_id(request)
    instructor = await session.get(Instructor, instructor_id)
    if instructor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INSTRUCTOR_NOT_FOUND,
        )

    generated_object_key = False
    if instructor.cover_image_url is None:
        object_key = build_profile_picture_object_key(
            picture_metadata.extension
        )
        instructor.cover_image_url = build_cover_image_url(
            settings,
            object_key,
        )
        generated_object_key = True
    else:
        object_key = object_key_from_profile_picture_url(
            instructor.cover_image_url,
            settings.minio_cover_bucket,
        )
        if object_key is None:
            logger.warning(
                "Could not parse the existing cover image URL: %s",
                instructor.cover_image_url,
            )
            object_key = build_profile_picture_object_key(
                picture_metadata.extension
            )
            instructor.cover_image_url = build_cover_image_url(
                settings,
                object_key,
            )
            generated_object_key = True

    try:
        await run_in_threadpool(
            upload_profile_picture,
            minio_client,
            settings.minio_cover_bucket,
            object_key,
            upload_data,
            picture_metadata.upload_content_type,
        )
    except Exception as exc:
        await session.rollback()
        if generated_object_key:
            await _delete_uploaded_picture(
                minio_client,
                settings.minio_cover_bucket,
                object_key,
            )
        logger.exception("Failed to upload an instructor cover image")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=COVER_IMAGE_UNAVAILABLE,
        ) from exc

    try:
        await session.commit()
    except Exception:
        await session.rollback()
        if generated_object_key:
            await _delete_uploaded_picture(
                minio_client,
                settings.minio_cover_bucket,
                object_key,
            )
        raise

    return InstructorCoverImageResponse(
        cover_image_url=instructor.cover_image_url,
    )


@router.delete(
    "/cover-image",
    response_model=InstructorMessageResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_instructor_cover_image(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> InstructorMessageResponse:
    """Delete the authenticated instructor's cover image."""

    settings: Settings = request.app.state.settings
    minio_client: Minio = request.app.state.minio

    instructor_id = _authenticated_instructor_id(request)
    instructor = await session.get(Instructor, instructor_id)
    if instructor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INSTRUCTOR_NOT_FOUND,
        )

    if instructor.cover_image_url is None:
        return InstructorMessageResponse(message=COVER_IMAGE_NOT_SET)

    object_key = object_key_from_profile_picture_url(
        instructor.cover_image_url,
        settings.minio_cover_bucket,
    )
    if object_key is not None:
        try:
            await run_in_threadpool(
                delete_profile_picture,
                minio_client,
                settings.minio_cover_bucket,
                object_key,
            )
        except Exception as exc:
            logger.exception("Failed to delete an instructor cover image")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=COVER_IMAGE_UNAVAILABLE,
            ) from exc
    else:
        logger.warning(
            "Could not parse the existing cover image URL: %s",
            instructor.cover_image_url,
        )

    instructor.cover_image_url = None
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return InstructorMessageResponse(message=COVER_IMAGE_DELETED)


@router.patch(
    "/verify-email",
    response_model=InstructorMessageResponse,
    status_code=status.HTTP_200_OK,
)
async def verify_email(
    payload: InstructorVerifyEmailRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> InstructorMessageResponse:
    """Verify an instructor's email address using a Redis action token."""

    redis: Redis = request.app.state.redis

    try:
        instructor_id_value = await _get_token_instructor_id(
            redis,
            payload.token,
        )
    except RedisError as exc:
        logger.exception("Redis failed while loading a verification token")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=EMAIL_VERIFICATION_UNAVAILABLE,
        ) from exc

    if instructor_id_value is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_VERIFICATION_TOKEN,
        )

    try:
        instructor_id = UUID(instructor_id_value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_VERIFICATION_TOKEN,
        ) from exc

    instructor = await session.get(Instructor, instructor_id)
    if instructor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INSTRUCTOR_NOT_FOUND,
        )

    if not instructor.is_email_verified:
        instructor.is_email_verified = True
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    await _delete_token(redis, payload.token, "verification")
    return InstructorMessageResponse(message="Email verified successfully")


@router.post(
    "/email",
    response_model=InstructorMessageResponse,
    status_code=status.HTTP_200_OK,
)
async def send_password_reset_email(
    payload: InstructorEmailRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> InstructorMessageResponse:
    """Send a password-reset email when the address is registered."""

    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis

    instructor = await _get_instructor_by_email(session, str(payload.email))
    if instructor is None:
        return InstructorMessageResponse(message=PASSWORD_RESET_SENT)

    await _store_password_reset_notification(
        session,
        redis,
        instructor,
        settings,
    )
    return InstructorMessageResponse(message=PASSWORD_RESET_SENT)


@router.patch(
    "/password-reset",
    response_model=InstructorMessageResponse,
    status_code=status.HTTP_200_OK,
)
async def reset_password(
    payload: InstructorPasswordResetRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> InstructorMessageResponse:
    """Reset an instructor's password using a Redis action token."""

    redis: Redis = request.app.state.redis

    try:
        instructor_id_value = await _get_token_instructor_id(
            redis,
            payload.token,
        )
    except RedisError as exc:
        logger.exception("Redis failed while loading a password reset token")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=PASSWORD_RESET_UNAVAILABLE,
        ) from exc

    if instructor_id_value is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_PASSWORD_RESET_TOKEN,
        )

    try:
        instructor_id = UUID(instructor_id_value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_PASSWORD_RESET_TOKEN,
        ) from exc

    instructor = await session.get(Instructor, instructor_id)
    if instructor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INSTRUCTOR_NOT_FOUND,
        )

    instructor.password_hash = hash(
        payload.password.get_secret_value()
    )
    if not instructor.is_email_verified:
        instructor.is_email_verified = True

    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    await _delete_token(redis, payload.token, "password reset")
    return InstructorMessageResponse(message="Password reset successfully")


__all__ = ["router"]
