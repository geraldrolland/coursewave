"""Instructor professional information routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.base_dependencies import get_session
from app.services.instructor.model import (
    Gender,
    Instructor,
    InstructorDetail,
    Onboard,
    OnboardingStatus,
    ProfessionalInfo,
)
from app.services.instructor.schema import (
    InstructorProfileCreateRequest,
    InstructorProfileUpdateRequest,
    InstructorSocialLinks,
)
from app.services.instructor.utils import (
    INSTRUCTOR_NOT_FOUND,
    _authenticated_instructor_id,
)

router = APIRouter()

PROFESSIONAL_INFO_ALREADY_EXISTS = "Professional info already exists"
PROFESSIONAL_INFO_NOT_FOUND = "Professional info not found"
INSTRUCTOR_DETAIL_NOT_FOUND = "Instructor detail not found"
ONBOARD_NOT_FOUND = "Onboard record not found"


async def _get_latest_instructor_detail(
    session: AsyncSession,
    instructor_id: UUID,
) -> InstructorDetail | None:
    """Return the instructor's most recently created detail record."""

    result = await session.exec(
        select(InstructorDetail)
        .where(
            col(InstructorDetail.instructor_id) == instructor_id
        )
        .order_by(col(InstructorDetail.created_at).desc())
        .limit(1)
    )
    return result.first()


async def _get_professional_info(
    session: AsyncSession,
    instructor_detail_id: UUID,
) -> ProfessionalInfo | None:
    """Return the professional info for an instructor detail."""

    result = await session.exec(
        select(ProfessionalInfo)
        .where(
            col(ProfessionalInfo.instructor_detail_id)
            == instructor_detail_id
        )
        .limit(1)
    )

    return result.first()


async def _load_instructor(
    session: AsyncSession,
    request: Request,
) -> Instructor:
    """Load the authenticated instructor."""

    instructor_id = _authenticated_instructor_id(request)
    instructor = await session.get(Instructor, instructor_id)
    if instructor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INSTRUCTOR_NOT_FOUND,
        )

    return instructor


async def _load_detail(
    session: AsyncSession,
    instructor: Instructor,
) -> InstructorDetail:
    """Load the instructor's latest detail record."""

    detail = await _get_latest_instructor_detail(
        session,
        instructor.instructor_id,
    )
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=INSTRUCTOR_DETAIL_NOT_FOUND,
        )

    return detail


async def _load_professional_info(
    session: AsyncSession,
    detail: InstructorDetail,
) -> ProfessionalInfo:
    """Load the professional info for an instructor detail."""

    professional_info = await _get_professional_info(
        session,
        detail.id,
    )
    if professional_info is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=PROFESSIONAL_INFO_NOT_FOUND,
        )

    return professional_info


def _to_response(
    professional_info: ProfessionalInfo,
) -> InstructorProfileCreateRequest:
    """Convert a ProfessionalInfo row to the profile response model."""

    return InstructorProfileCreateRequest(
        first_name=professional_info.first_name,
        middle_name=professional_info.middle_name,
        last_name=professional_info.last_name,
        gender=professional_info.gender.value,
        country_of_residence=professional_info.country_of_residence,
        state=professional_info.state,
        city=professional_info.city,
        bio=professional_info.bio,
        headline=professional_info.headline,
        expertise=professional_info.expertise,
        years_of_exp=professional_info.years_of_exp,
        current_occupation=professional_info.current_occupation,
        company=professional_info.company,
        social_links=[
            InstructorSocialLinks.model_validate(link)
            for link in professional_info.social_links
        ],
    )


@router.post(
    "/professional_info",
    response_model=InstructorProfileCreateRequest,
    response_model_exclude_none=True,
    status_code=status.HTTP_200_OK,
)
async def create_professional_info(
    payload: InstructorProfileCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> InstructorProfileCreateRequest:
    """Create professional info for the instructor's latest detail."""

    instructor = await _load_instructor(session, request)
    detail = await _load_detail(session, instructor)

    existing = await _get_professional_info(session, detail.id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=PROFESSIONAL_INFO_ALREADY_EXISTS,
        )

    onboard_result = await session.exec(
        select(Onboard)
        .where(col(Onboard.instructor_id) == instructor.instructor_id)
        .with_for_update()
    )
    onboard = onboard_result.first()
    if onboard is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ONBOARD_NOT_FOUND,
        )

    onboard.onboarding_step += 1
    if onboard.onboarding_step == 6:
        onboard.onboarding_status = OnboardingStatus.COMPLETED
        onboard.is_onboarded = True
    elif onboard.onboarding_status is OnboardingStatus.NOT_STARTED:
        onboard.onboarding_status = OnboardingStatus.IN_PROGRESS

    professional_info = ProfessionalInfo(
        instructor_id=instructor.instructor_id,
        instructor_detail_id=detail.id,
        first_name=payload.first_name,
        middle_name=payload.middle_name,
        last_name=payload.last_name,
        gender=Gender(payload.gender),
        country_of_residence=payload.country_of_residence,
        state=payload.state,
        city=payload.city,
        bio=payload.bio,
        headline=payload.headline,
        expertise=payload.expertise,
        years_of_exp=payload.years_of_exp,
        current_occupation=payload.current_occupation,
        company=payload.company,
        social_links=[
            link.model_dump(mode="json", exclude_none=True)
            for link in payload.social_links
        ],
    )

    session.add(professional_info)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return payload


@router.get(
    "/professional_info",
    response_model=InstructorProfileCreateRequest,
    response_model_exclude_none=True,
    status_code=status.HTTP_200_OK,
)
async def get_professional_info(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> InstructorProfileCreateRequest:
    """Return professional info for the instructor's latest detail."""

    instructor = await _load_instructor(session, request)
    detail = await _load_detail(session, instructor)
    professional_info = await _load_professional_info(session, detail)
    return _to_response(professional_info)


@router.patch(
    "/professional_info",
    response_model=InstructorProfileCreateRequest,
    response_model_exclude_none=True,
    status_code=status.HTTP_200_OK,
)
async def update_professional_info(
    payload: InstructorProfileUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> InstructorProfileCreateRequest:
    """Update professional info for the instructor's latest detail."""

    instructor = await _load_instructor(session, request)
    detail = await _load_detail(session, instructor)
    professional_info = await _load_professional_info(session, detail)

    updates = payload.model_dump(
        mode="json",
        exclude_none=True,
        exclude_unset=True,
    )
    gender = updates.pop("gender", None)
    if gender is not None:
        professional_info.gender = Gender(gender)

    for field_name, value in updates.items():
        setattr(professional_info, field_name, value)

    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return _to_response(professional_info)


__all__ = [
    "INSTRUCTOR_DETAIL_NOT_FOUND",
    "ONBOARD_NOT_FOUND",
    "PROFESSIONAL_INFO_ALREADY_EXISTS",
    "PROFESSIONAL_INFO_NOT_FOUND",
    "router",
]
