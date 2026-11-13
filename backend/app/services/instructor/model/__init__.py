"""Database models for the instructor service."""

from app.services.instructor.model.education import Degree, Education
from app.services.instructor.model.experience import Experience
from app.services.instructor.model.instructor import AuthMethod, Instructor
from app.services.instructor.model.payment_info import PaymentInfo
from app.services.instructor.model.profile import Gender, InstructorProfile

__all__ = [
    "AuthMethod",
    "Degree",
    "Education",
    "Experience",
    "Gender",
    "Instructor",
    "InstructorProfile",
    "PaymentInfo",
]
