"""Request and response schemas for the instructor service."""

from pathlib import PurePath
from typing import Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    SecretStr,
    field_validator,
    model_validator,
)

from app.services.instructor.model import Gender
from app.services.instructor.utils import (
    _normalize_email,
    _normalize_token,
    validate_password_strength,
)

PROFILE_PICTURE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png"})
PROFILE_PICTURE_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
PROFILE_PICTURE_MAX_BYTES = 5 * 1024 * 1024


class InstructorRegisterRequest(BaseModel):
    """Payload accepted by the instructor registration endpoint."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: SecretStr
    confirm_password: SecretStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        return validate_password_strength(value)

    @model_validator(mode="after")
    def validate_password_confirmation(self) -> Self:
        if (
            self.password.get_secret_value()
            != self.confirm_password.get_secret_value()
        ):
            raise ValueError("Password and confirm password do not match")

        return self


class InstructorRegisterResponse(BaseModel):
    """Successful instructor registration response."""

    instructor_id: UUID
    message: str


class InstructorLoginRequest(BaseModel):
    """Payload accepted by the instructor login endpoint."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: SecretStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)


class InstructorLoginResponse(BaseModel):
    """Successful instructor login response."""

    access_token: str = Field(serialization_alias="accessToken")


class InstructorVerifyOtpRequest(BaseModel):
    """Payload accepted by the instructor OTP verification endpoint."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    token: str = Field(min_length=1, max_length=256)

    @field_validator("token")
    @classmethod
    def normalize_token(cls, value: str) -> str:
        return _normalize_token(value)


class InstructorResendOtpRequest(BaseModel):
    """Payload accepted by the instructor OTP resend endpoint."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=256)

    @field_validator("token")
    @classmethod
    def normalize_token(cls, value: str) -> str:
        return _normalize_token(value)


class InstructorVerifyEmailRequest(BaseModel):
    """Payload accepted by the instructor email verification endpoint."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=256)

    @field_validator("token")
    @classmethod
    def normalize_token(cls, value: str) -> str:
        return _normalize_token(value)


class InstructorEmailRequest(BaseModel):
    """Payload accepted by the password-reset email endpoint."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)


class InstructorPasswordResetRequest(BaseModel):
    """Payload accepted by the instructor password reset endpoint."""

    model_config = ConfigDict(extra="forbid")

    password: SecretStr
    confirm_password: SecretStr
    token: str = Field(min_length=1, max_length=256)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        return validate_password_strength(value)

    @field_validator("token")
    @classmethod
    def normalize_token(cls, value: str) -> str:
        return _normalize_token(value)

    @model_validator(mode="after")
    def validate_password_confirmation(self) -> Self:
        if (
            self.password.get_secret_value()
            != self.confirm_password.get_secret_value()
        ):
            raise ValueError("Password and confirm password do not match")

        return self


class InstructorMessageResponse(BaseModel):
    """Generic message response for instructor account actions."""

    message: str


class InstructorMeResponse(BaseModel):
    """Identity of the currently authenticated instructor."""

    id: str
    email: EmailStr


class InstructorSocialLinks(BaseModel):
    """Social links accepted in an instructor profile."""

    model_config = ConfigDict(extra="forbid")

    linkedin: HttpUrl | None = None
    x: HttpUrl | None = None

    @model_validator(mode="after")
    def validate_at_least_one_link(self) -> Self:
        if self.linkedin is None and self.x is None:
            raise ValueError("At least one social link must be provided")

        return self


class InstructorProfileCreateRequest(BaseModel):
    """Fields accepted by the instructor profile creation endpoint."""

    model_config = ConfigDict(extra="forbid")

    first_name: str = Field(min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    gender: Gender
    country_of_residence: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=100)
    bio: str = Field(min_length=1, max_length=1000)
    headline: str = Field(min_length=1, max_length=255)
    current_occupation: str = Field(min_length=1, max_length=255)
    years_of_exp: int = Field(ge=0)
    expertise: list[str] = Field(min_length=1)
    social_links: list[InstructorSocialLinks] | None = Field(
        default=None,
        min_length=1,
    )
    company: str | None = Field(default=None, max_length=255)

    @field_validator(
        "first_name",
        "last_name",
        "country_of_residence",
        "state",
        "city",
        "bio",
        "headline",
        "current_occupation",
    )
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Field cannot be empty")
        return value

    @field_validator("middle_name", "company")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None

        value = value.strip()
        if not value:
            raise ValueError("Field cannot be empty")
        return value

    @field_validator("expertise")
    @classmethod
    def validate_expertise(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            item = item.strip()
            if not item:
                raise ValueError("Expertise entries cannot be empty")
            normalized.append(item)

        return normalized


class InstructorProfilePictureMetadata(BaseModel):
    """Validated metadata for an instructor profile picture."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    size: int
    content_type: str | None = None

    @field_validator("filename")
    @classmethod
    def validate_extension(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Profile picture filename cannot be empty")

        if PurePath(value).suffix.lower() not in PROFILE_PICTURE_EXTENSIONS:
            raise ValueError(
                "Profile picture must be a .jpg, .jpeg, or .png file"
            )

        return value

    @field_validator("size")
    @classmethod
    def validate_size(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Profile picture cannot be empty")

        if value >= PROFILE_PICTURE_MAX_BYTES:
            raise ValueError("Profile picture must be smaller than 5 MB")

        return value

    @property
    def extension(self) -> str:
        """Return the normalized file extension."""

        return PurePath(self.filename).suffix.lower()

    @property
    def upload_content_type(self) -> str:
        """Return the content type derived from the file extension."""

        return PROFILE_PICTURE_CONTENT_TYPES[self.extension]


class InstructorProfileResponse(BaseModel):
    """Profile fields returned after successful creation."""

    profile_id: UUID
    instructor_id: UUID
    first_name: str
    middle_name: str | None
    last_name: str
    email: EmailStr
    gender: Gender
    country_of_residence: str
    state: str | None
    city: str
    bio: str
    headline: str
    profile_pic_url: str
    expertise: list[str]
    years_of_exp: int
    current_occupation: str
    company: str | None
    social_links: list[dict[str, HttpUrl]] | None


__all__ = [
    "InstructorEmailRequest",
    "InstructorLoginRequest",
    "InstructorLoginResponse",
    "InstructorMeResponse",
    "InstructorMessageResponse",
    "InstructorPasswordResetRequest",
    "InstructorProfileCreateRequest",
    "InstructorProfilePictureMetadata",
    "InstructorProfileResponse",
    "InstructorRegisterRequest",
    "InstructorRegisterResponse",
    "InstructorResendOtpRequest",
    "InstructorSocialLinks",
    "InstructorVerifyEmailRequest",
    "InstructorVerifyOtpRequest",
    "PROFILE_PICTURE_CONTENT_TYPES",
    "PROFILE_PICTURE_EXTENSIONS",
    "PROFILE_PICTURE_MAX_BYTES",
]
