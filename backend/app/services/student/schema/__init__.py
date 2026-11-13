"""Request and response schemas for the student service."""

from pathlib import PurePath
from typing import Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from app.services.student.utils import (
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


class StudentRegisterRequest(BaseModel):
    """Payload accepted by the student registration endpoint."""

    model_config = ConfigDict(extra="forbid")

    first_name: str = Field(min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: SecretStr
    confirm_password: SecretStr

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_required_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be empty")
        return value

    @field_validator("middle_name")
    @classmethod
    def normalize_middle_name(cls, value: str | None) -> str | None:
        if value is None:
            return None

        value = value.strip()
        return value or None

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


class StudentLoginRequest(BaseModel):
    """Payload accepted by the student login endpoint."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: SecretStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)


class StudentVerifyEmailRequest(BaseModel):
    """Payload accepted by the email verification endpoint."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=256)

    @field_validator("token")
    @classmethod
    def normalize_token(cls, value: str) -> str:
        return _normalize_token(value)


class StudentEmailRequest(BaseModel):
    """Payload accepted by the password-reset email endpoint."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)


class StudentPasswordResetRequest(BaseModel):
    """Payload accepted by the password reset endpoint."""

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


class StudentRegisterResponse(BaseModel):
    """Successful student registration response."""

    user_id: UUID
    message: str


class StudentLoginResponse(BaseModel):
    """Successful student login response."""

    access_token: str = Field(serialization_alias="accessToken")


class StudentMessageResponse(BaseModel):
    """Generic message response for student account actions."""

    message: str


class StudentMeResponse(BaseModel):
    """Identity of the currently authenticated student."""

    id: str
    email: EmailStr


class StudentProfileUpdateRequest(BaseModel):
    """Profile fields accepted by the multipart update endpoint."""

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, max_length=100)
    middlename: str | None = Field(default=None, max_length=100)
    has_profile_pic: bool = False

    @field_validator("first_name", "middlename")
    @classmethod
    def validate_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None

        value = value.strip()
        if not value:
            raise ValueError("Name cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_has_changes(self) -> Self:
        if (
            self.first_name is None
            and self.middlename is None
            and not self.has_profile_pic
        ):
            raise ValueError("At least one profile field must be provided")

        return self


class ProfilePictureMetadata(BaseModel):
    """Validated metadata for an uploaded profile picture."""

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


class StudentProfileResponse(BaseModel):
    """Profile fields returned after a successful profile update."""

    user_id: UUID
    first_name: str
    middle_name: str | None
    last_name: str
    email: EmailStr
    profile_pic_url: str | None


class StudentProfileGetResponse(BaseModel):
    """Profile fields returned by the current-student profile lookup."""

    first_name: str
    last_name: str
    email: EmailStr
    profile_pic_url: str | None
