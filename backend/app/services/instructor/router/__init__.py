"""Instructor authentication and profile router package."""

from app.services.instructor.router.instructor import router
from app.services.instructor.router.profile import router as profile_router

__all__ = ["profile_router", "router"]
