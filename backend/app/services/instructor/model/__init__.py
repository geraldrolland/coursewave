"""Database models for the instructor service."""

from app.services.instructor.model.education import DegreeType, Education
from app.services.instructor.model.instructor import AuthMethod, Instructor
from app.services.instructor.model.instructor_detail import (
    InstructorDetail,
    InstructorVerificationStatus,
)
from app.services.instructor.model.onboard import Onboard, OnboardingStatus
from app.services.instructor.model.payment_info import PaymentInfo
from app.services.instructor.model.professional_info import (
    Gender,
    ProfessionalInfo,
)

__all__ = [
    "AuthMethod",
    "DegreeType",
    "Education",
    "Gender",
    "Instructor",
    "InstructorDetail",
    "InstructorVerificationStatus",
    "Onboard",
    "OnboardingStatus",
    "PaymentInfo",
    "ProfessionalInfo",
]
