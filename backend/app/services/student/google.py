"""Google OAuth models used by student authentication."""

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_HTTP_TIMEOUT_SECONDS = 10.0


class GoogleAuthError(Exception):
    """Raised when Google authentication cannot be completed."""


class GoogleUserInfo(BaseModel):
    """Subset of the Google userinfo response used by CourseWave."""

    model_config = ConfigDict(extra="ignore")

    email: EmailStr
    email_verified: bool = Field(
        default=False,
        validation_alias=AliasChoices("email_verified", "verified_email"),
    )
    given_name: str | None = None
    family_name: str | None = None
    name: str | None = None
    picture: str | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


__all__ = [
    "GOOGLE_HTTP_TIMEOUT_SECONDS",
    "GOOGLE_TOKEN_URL",
    "GOOGLE_USERINFO_URL",
    "GoogleAuthError",
    "GoogleUserInfo",
]
