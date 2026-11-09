"""Tests for student registration request validation."""

import pytest
from pydantic import ValidationError

from app.services.student.schema import (
    StudentEmailRequest,
    StudentLoginRequest,
    StudentPasswordResetRequest,
    StudentRegisterRequest,
    StudentVerifyEmailRequest,
)


def registration_payload(**overrides: object) -> dict[str, object]:
    """Return a valid registration payload with optional overrides."""

    payload: dict[str, object] = {
        "first_name": "Ada",
        "middle_name": "Augusta",
        "last_name": "Lovelace",
        "email": "ada@example.com",
        "password": "StrongPass1!",
        "confirm_password": "StrongPass1!",
    }
    payload.update(overrides)

    if "password" in overrides and "confirm_password" not in overrides:
        payload["confirm_password"] = overrides["password"]

    return payload


def test_valid_payload_normalizes_names_and_email() -> None:
    request = StudentRegisterRequest(
        **registration_payload(
            first_name="  Ada  ",
            middle_name="  Augusta  ",
            last_name="  Lovelace  ",
            email="ADA@EXAMPLE.COM",
        )
    )

    assert request.first_name == "Ada"
    assert request.middle_name == "Augusta"
    assert request.last_name == "Lovelace"
    assert request.email == "ada@example.com"
    assert request.password.get_secret_value() == "StrongPass1!"


def test_middle_name_is_optional_and_blank_becomes_none() -> None:
    without_middle = StudentRegisterRequest(
        **registration_payload(middle_name=None)
    )
    blank_middle = StudentRegisterRequest(**registration_payload(middle_name="   "))

    assert without_middle.middle_name is None
    assert blank_middle.middle_name is None


@pytest.mark.parametrize(
    "password",
    [
        "Ab1!",  # Too short.
        "lowercase1!",  # Missing uppercase.
        "UPPERCASE1!",  # Missing lowercase.
        "NoDigits!!",  # Missing digit.
        "NoSpecial1",  # Missing special character.
        "Has Space1",  # Whitespace is not a special character.
    ],
)
def test_weak_passwords_are_rejected(password: str) -> None:
    with pytest.raises(ValidationError):
        StudentRegisterRequest(**registration_payload(password=password))


def test_password_at_minimum_length_is_accepted() -> None:
    request = StudentRegisterRequest(
        **registration_payload(password="Abcdef1!", confirm_password="Abcdef1!")
    )

    assert request.password.get_secret_value() == "Abcdef1!"


def test_password_longer_than_maximum_is_rejected() -> None:
    password = f"Aa1!{'x' * 125}"

    with pytest.raises(ValidationError):
        StudentRegisterRequest(**registration_payload(password=password))


def test_password_confirmation_must_match() -> None:
    with pytest.raises(ValidationError):
        StudentRegisterRequest(
            **registration_payload(
                password="StrongPass1!",
                confirm_password="StrongPass2!",
            )
        )


@pytest.mark.parametrize("email", ["not-an-email", "missing-at.example.com", ""])
def test_invalid_email_is_rejected(email: str) -> None:
    with pytest.raises(ValidationError):
        StudentRegisterRequest(**registration_payload(email=email))


def test_last_name_is_required() -> None:
    payload = registration_payload()
    payload.pop("last_name")

    with pytest.raises(ValidationError):
        StudentRegisterRequest(**payload)


@pytest.mark.parametrize("field", ["first_name", "last_name"])
def test_required_names_cannot_be_blank(field: str) -> None:
    with pytest.raises(ValidationError):
        StudentRegisterRequest(**registration_payload(**{field: "   "}))


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        StudentRegisterRequest(**registration_payload(role="admin"))


def test_login_request_normalizes_email_and_allows_any_password() -> None:
    request = StudentLoginRequest(
        email=" ADA@EXAMPLE.COM ",
        password="weak",
    )

    assert request.email == "ada@example.com"
    assert request.password.get_secret_value() == "weak"


def test_login_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        StudentLoginRequest(
            email="ada@example.com",
            password="StrongPass1!",
            role="admin",
        )


def test_verify_email_request_trims_token() -> None:
    request = StudentVerifyEmailRequest(token="  verification-token  ")

    assert request.token == "verification-token"


@pytest.mark.parametrize("token", ["", "   "])
def test_verify_email_request_rejects_blank_token(token: str) -> None:
    with pytest.raises(ValidationError):
        StudentVerifyEmailRequest(token=token)


def test_verify_email_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        StudentVerifyEmailRequest(token="abc", user_id="123")


def test_email_request_normalizes_address() -> None:
    request = StudentEmailRequest(email=" ADA@EXAMPLE.COM ")

    assert request.email == "ada@example.com"


def test_email_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        StudentEmailRequest(email="ada@example.com", role="student")


def password_reset_payload(**overrides: object) -> dict[str, object]:
    """Return a valid password reset payload with optional overrides."""

    payload: dict[str, object] = {
        "password": "StrongPass1!",
        "confirm_password": "StrongPass1!",
        "token": "reset-token",
    }
    payload.update(overrides)

    if "password" in overrides and "confirm_password" not in overrides:
        payload["confirm_password"] = overrides["password"]

    return payload


def test_password_reset_request_accepts_valid_payload() -> None:
    request = StudentPasswordResetRequest(
        **password_reset_payload(token="  reset-token  ")
    )

    assert request.token == "reset-token"
    assert request.password.get_secret_value() == "StrongPass1!"


@pytest.mark.parametrize(
    "password",
    [
        "Ab1!",  # Too short.
        "lowercase1!",  # Missing uppercase.
        "UPPERCASE1!",  # Missing lowercase.
        "NoDigits!!",  # Missing digit.
        "NoSpecial1",  # Missing special character.
        "Has Space1",  # Whitespace is not a special character.
    ],
)
def test_password_reset_rejects_weak_passwords(password: str) -> None:
    with pytest.raises(ValidationError):
        StudentPasswordResetRequest(**password_reset_payload(password=password))


def test_password_reset_requires_matching_confirmation() -> None:
    with pytest.raises(ValidationError):
        StudentPasswordResetRequest(
            **password_reset_payload(
                password="StrongPass1!",
                confirm_password="StrongPass2!",
            )
        )


@pytest.mark.parametrize("token", ["", "   "])
def test_password_reset_rejects_blank_token(token: str) -> None:
    with pytest.raises(ValidationError):
        StudentPasswordResetRequest(**password_reset_payload(token=token))


def test_password_reset_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        StudentPasswordResetRequest(**password_reset_payload(role="admin"))
