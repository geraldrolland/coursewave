"""Instructor profile routes."""

import logging

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from minio import Minio
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.base_config import Settings
from app.base_dependencies import get_session
from app.services.instructor.model import Instructor, InstructorProfile
from app.services.instructor.schema import (
    InstructorProfileCreateRequest,
    InstructorProfileResponse,
)
from app.services.instructor.utils import (
    INSTRUCTOR_NOT_FOUND,
    _authenticated_instructor_id,
    _decode_expertise,
    _decode_social_links,
    _delete_uploaded_picture,
    _read_profile_picture,
)
from app.services.storage.minio import (
    build_profile_picture_object_key,
    build_profile_picture_url,
    upload_profile_picture,
)

router = APIRouter(prefix="/instructor/api/v1", tags=["instructors"])
logger = logging.getLogger("instructor_service.router.profile")

PROFILE_ALREADY_EXISTS = "Instructor profile already exists"
PROFILE_PICTURE_UNAVAILABLE = "Profile picture service unavailable"


@router.post(
    "/profile",
    response_model=InstructorProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_instructor_profile(
    request: Request,
    first_name: str = Form(...),
    middle_name: str | None = Form(default=None),
    last_name: str = Form(...),
    gender: str = Form(...),
    country_of_residence: str = Form(...),
    state: str = Form(...),
    city: str = Form(...),
    bio: str = Form(...),
    headline: str = Form(...),
    profile_pic: UploadFile = File(...),
    current_occupation: str = Form(...),
    years_of_exp: int = Form(...),
    expertise: list[str] = Form(...),
    social_links: list[str] | None = Form(default=None),
    company: str | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
) -> InstructorProfileResponse:
    """Create an instructor profile and upload the required picture."""

    settings: Settings = request.app.state.settings
    minio_client: Minio = request.app.state.minio

    try:
        profile_request = InstructorProfileCreateRequest(
            first_name=first_name,
            middle_name=middle_name,
            last_name=last_name,
            gender=gender,
            country_of_residence=country_of_residence,
            state=state,
            city=city,
            bio=bio,
            headline=headline,
            current_occupation=current_occupation,
            years_of_exp=years_of_exp,
            expertise=_decode_expertise(expertise),
            social_links=(
                _decode_social_links(social_links)
                if social_links is not None
                else None
            ),
            company=company,
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc

    instructor_id = _authenticated_instructor_id(request)

    instructor = await session.get(Instructor, instructor_id)
    if instructor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INSTRUCTOR_NOT_FOUND,
        )

    result = await session.execute(
        select(InstructorProfile).where(
            InstructorProfile.instructor_id == instructor_id
        )
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=PROFILE_ALREADY_EXISTS,
        )

    upload_data, picture_metadata = await _read_profile_picture(profile_pic)

    object_key = build_profile_picture_object_key(
        picture_metadata.extension
    )

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
        logger.exception("Failed to upload an instructor profile picture")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=PROFILE_PICTURE_UNAVAILABLE,
        ) from exc

    profile = InstructorProfile(
        instructor_id=instructor.instructor_id,
        first_name=profile_request.first_name,
        middle_name=profile_request.middle_name,
        email=instructor.email,
        last_name=profile_request.last_name,
        gender=profile_request.gender,
        country_of_residence=profile_request.country_of_residence,
        state=profile_request.state,
        city=profile_request.city,
        bio=profile_request.bio,
        headline=profile_request.headline,
        profile_pic_url=build_profile_picture_url(settings, object_key),
        expertise=profile_request.expertise,
        years_of_exp=profile_request.years_of_exp,
        current_occupation=profile_request.current_occupation,
        company=profile_request.company,
        social_links=(
            [
                link.model_dump(mode="json", exclude_none=True)
                for link in profile_request.social_links
            ]
            if profile_request.social_links is not None
            else None
        ),
    )

    session.add(profile)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        await _delete_uploaded_picture(
            minio_client,
            settings.minio_bucket,
            object_key,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=PROFILE_ALREADY_EXISTS,
        ) from exc
    except Exception:
        await session.rollback()
        await _delete_uploaded_picture(
            minio_client,
            settings.minio_bucket,
            object_key,
        )
        raise

    return InstructorProfileResponse(
        profile_id=profile.profile_id,
        instructor_id=profile.instructor_id,
        first_name=profile.first_name,
        middle_name=profile.middle_name,
        last_name=profile.last_name,
        email=profile.email,
        gender=profile.gender,
        country_of_residence=profile.country_of_residence,
        state=profile.state,
        city=profile.city,
        bio=profile.bio,
        headline=profile.headline,
        profile_pic_url=profile.profile_pic_url,
        expertise=profile.expertise,
        years_of_exp=profile.years_of_exp,
        current_occupation=profile.current_occupation,
        company=profile.company,
        social_links=profile.social_links,
    )


__all__ = [
    "PROFILE_ALREADY_EXISTS",
    "PROFILE_PICTURE_UNAVAILABLE",
    "router",
]
