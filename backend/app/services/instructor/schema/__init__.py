"""Request and response schemas for the instructor service."""

from pathlib import PurePath
from typing import Literal, Self
from uuid import UUID

from fastapi import UploadFile
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

from app.services.instructor.utils import (
    _normalize_email,
    _normalize_token,
    validate_password_strength,
)

INSTRUCTOR_PROFILE_PICTURE_EXTENSIONS = frozenset({".jpg", ".jpeg"})
INSTRUCTOR_PROFILE_PICTURE_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
INSTRUCTOR_PROFILE_PICTURE_MAX_BYTES = 5 * 1024 * 1024


def _validate_instructor_image(upload: UploadFile) -> None:
    """Validate an instructor image upload's filename and size."""

    filename = (upload.filename or "").strip()
    if not filename:
        raise ValueError("Image filename cannot be empty")

    if (
        PurePath(filename).suffix.lower()
        not in INSTRUCTOR_PROFILE_PICTURE_EXTENSIONS
    ):
        raise ValueError("Image must be a .jpg or .jpeg file")

    if upload.size is not None:
        if upload.size <= 0:
            raise ValueError("Image cannot be empty")

        if upload.size >= INSTRUCTOR_PROFILE_PICTURE_MAX_BYTES:
            raise ValueError("Image must be smaller than 5 MB")


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

        if (
            PurePath(value).suffix.lower()
            not in INSTRUCTOR_PROFILE_PICTURE_EXTENSIONS
        ):
            raise ValueError(
                "Profile picture must be a .jpg or .jpeg file"
            )

        return value

    @field_validator("size")
    @classmethod
    def validate_size(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Profile picture cannot be empty")

        if value >= INSTRUCTOR_PROFILE_PICTURE_MAX_BYTES:
            raise ValueError("Profile picture must be smaller than 5 MB")

        return value

    @property
    def extension(self) -> str:
        """Return the normalized file extension."""

        return PurePath(self.filename).suffix.lower()

    @property
    def upload_content_type(self) -> str:
        """Return the content type derived from the file extension."""

        return INSTRUCTOR_PROFILE_PICTURE_CONTENT_TYPES[self.extension]


class InstructorProfilePicForm(BaseModel):
    """Multipart form accepted by the instructor profile-picture endpoint."""

    model_config = ConfigDict(extra="forbid")

    profile_pic: list[UploadFile] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_profile_picture(self) -> Self:
        if len(self.profile_pic) != 1:
            raise ValueError("Exactly one profile picture is required")

        _validate_instructor_image(self.profile_pic[0])
        return self

    @property
    def picture(self) -> UploadFile:
        """Return the single validated upload."""

        return self.profile_pic[0]


class InstructorCoverImageForm(BaseModel):
    """Multipart form accepted by the instructor cover-image endpoint."""

    model_config = ConfigDict(extra="forbid")

    cover_image: list[UploadFile] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_cover_image(self) -> Self:
        if len(self.cover_image) != 1:
            raise ValueError("Exactly one cover image is required")

        _validate_instructor_image(self.cover_image[0])
        return self

    @property
    def picture(self) -> UploadFile:
        """Return the single validated upload."""

        return self.cover_image[0]


class InstructorProfilePicResponse(BaseModel):
    """Profile picture URL returned after an upload."""

    profile_pic_url: str


class InstructorCoverImageResponse(BaseModel):
    """Cover image URL returned after an upload."""

    cover_image_url: str


class InstructorSocialLinks(BaseModel):
    """Social links stored in an instructor's JSON profile."""

    model_config = ConfigDict(extra="forbid")

    linkedin: HttpUrl | None = None
    x: HttpUrl | None = None

    @model_validator(mode="after")
    def validate_at_least_one_link(self) -> Self:
        if self.linkedin is None and self.x is None:
            raise ValueError("At least one social link must be provided")

        return self


class InstructorProfileUpdateRequest(BaseModel):
    """Partial JSON profile payload accepted by the PATCH endpoint."""

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    gender: Literal["male", "female"] | None = None
    country_of_residence: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    bio: str | None = Field(default=None, max_length=1000)
    headline: str | None = Field(default=None, max_length=255)
    expertise: list[str] | None = Field(default=None, min_length=1)
    years_of_exp: int | None = Field(default=None, gt=0)
    current_occupation: str | None = Field(default=None, max_length=255)
    company: str | None = Field(default=None, max_length=255)
    social_links: list[InstructorSocialLinks] | None = Field(
        default=None,
        min_length=1,
    )

    @field_validator(
        "first_name",
        "middle_name",
        "last_name",
        "country_of_residence",
        "state",
        "city",
        "bio",
        "headline",
        "current_occupation",
        "company",
    )
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
    def validate_expertise(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None

        normalized: list[str] = []
        for item in value:
            item = item.strip()
            if not item:
                raise ValueError("Expertise entries cannot be empty")
            normalized.append(item)

        return normalized


class InstructorProfileCreateRequest(BaseModel):
    """JSON profile payload accepted by the instructor profile endpoint."""

    model_config = ConfigDict(extra="forbid")

    first_name: str = Field(min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    gender: Literal["male", "female"]
    country_of_residence: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=100)
    bio: str = Field(min_length=1, max_length=1000)
    headline: str = Field(min_length=1, max_length=255)
    expertise: list[str] = Field(min_length=1)
    years_of_exp: int = Field(gt=0)
    current_occupation: str = Field(min_length=1, max_length=255)
    company: str | None = Field(default=None, max_length=255)
    social_links: list[InstructorSocialLinks] = Field(min_length=1)

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


__all__ = [
    "InstructorEmailRequest",
    "InstructorLoginRequest",
    "InstructorLoginResponse",
    "InstructorMeResponse",
    "InstructorMessageResponse",
    "InstructorCoverImageForm",
    "InstructorCoverImageResponse",
    "InstructorPasswordResetRequest",
    "InstructorProfileCreateRequest",
    "InstructorProfilePicForm",
    "InstructorProfilePicResponse",
    "InstructorProfilePictureMetadata",
    "InstructorProfileUpdateRequest",
    "InstructorRegisterRequest",
    "InstructorRegisterResponse",
    "InstructorResendOtpRequest",
    "InstructorSocialLinks",
    "InstructorVerifyEmailRequest",
    "InstructorVerifyOtpRequest",
    "INSTRUCTOR_PROFILE_PICTURE_CONTENT_TYPES",
    "INSTRUCTOR_PROFILE_PICTURE_EXTENSIONS",
    "INSTRUCTOR_PROFILE_PICTURE_MAX_BYTES",
]
