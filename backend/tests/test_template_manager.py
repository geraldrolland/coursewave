"""Tests for the email template manager."""

from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path

import pytest

from app.services.notification.template_manager import (
    TemplateManager,
    TemplateType,
    template,
    template_registery,
)


def test_template_registry_covers_supported_types() -> None:
    assert set(template_registery) == {
        TemplateType.OTP,
        TemplateType.EMAIL_VERIFICATION,
        TemplateType.FORGOT_PASSWORD,
    }
    for config in template_registery.values():
        assert set(config) == {"template_dir", "template"}


def test_get_template_renders_forgot_password_link() -> None:
    manager = TemplateManager(
        "forgot_password",
        {"link": "https://example.com/forgot-password?token=abc"},
    )

    result = manager.get_template()

    assert result.version_number == 1
    assert result.template_path == (
        "templates/email/forgot_password/v1.html"
    )
    assert (
        '<a href="https://example.com/forgot-password?token=abc">'
        "Reset password</a>"
        in result.body
    )
    assert "<!DOCTYPE html>" in result.body


def test_get_template_renders_email_verification_link() -> None:
    manager = TemplateManager(
        "email_verification",
        {"link": "https://example.com/verify?token=abc"},
    )

    result = manager.get_template()

    assert is_dataclass(result)
    assert isinstance(result, template)
    assert result.version_number == 1
    assert result.template_path == (
        "templates/email/email_verification/v1.html"
    )
    assert (
        '<a href="https://example.com/verify?token=abc">Verify email</a>'
        in result.body
    )
    assert "<!DOCTYPE html>" in result.body


def test_get_template_accepts_enum_and_renders_otp() -> None:
    manager = TemplateManager(TemplateType.OTP, {"otp_code": "123456"})

    result = manager.get_template()

    assert result.version_number == 1
    assert result.template_path == "templates/email/otp/v1.html"
    assert "<strong>123456</strong>" in result.body


def test_template_build_returns_rendered_html() -> None:
    body = TemplateManager.template_build(
        "templates/email/email_verification/v1.html",
        {"link": "https://example.com/verify"},
    )

    assert 'href="https://example.com/verify"' in body
    assert "{{ link }}" not in body


def test_template_is_frozen() -> None:
    result = TemplateManager("otp", {"otp_code": "123456"}).get_template()

    with pytest.raises(FrozenInstanceError):
        result.body = "Changed"


def test_unknown_template_type_raises_value_error() -> None:
    with pytest.raises(ValueError):
        TemplateManager("unknown", {})


def test_missing_template_file_raises_file_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "missing.html"

    with pytest.raises(FileNotFoundError):
        TemplateManager.template_build(missing, {})
