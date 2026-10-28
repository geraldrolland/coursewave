"""Request and response schemas for the student service."""

import re
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

PASSWORD_PATTERN = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9\s]).{8,}$"
)


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
        return value.strip().lower()

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        password = value.get_secret_value()

        if len(password) > 128:
            raise ValueError("Password must be at most 128 characters")

        if not PASSWORD_PATTERN.fullmatch(password):
            raise ValueError(
                "Password must be at least 8 characters and contain "
                "an uppercase letter, a lowercase letter, a number, "
                "and a special character"
            )

        return value

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
