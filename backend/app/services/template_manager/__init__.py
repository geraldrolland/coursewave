"""Email template registration and rendering."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

BACKEND_ROOT = Path(__file__).resolve().parents[3]


class TemplateType(str, Enum):
    """Supported email template types."""

    OTP = "otp"
    EMAIL_VERIFICATION = "email_verification"


@dataclass(frozen=True)
class template:
    """A rendered email template."""

    subject: str
    body: str


template_registery: dict[TemplateType, dict[str, str]] = {
    TemplateType.OTP: {
        "subject": "Your CourseWave verification code",
        "template_dir": "templates/email/otp",
        "template": "v1.html",
    },
    TemplateType.EMAIL_VERIFICATION: {
        "subject": "Verify your CourseWave email",
        "template_dir": "templates/email/email_verification",
        "template": "v1.html",
    },
}


class TemplateManager:
    """Render a registered email template with the supplied content."""

    def __init__(
        self,
        type: str | TemplateType,
        content: dict[str, Any],
    ) -> None:
        self.type = TemplateType(type)
        self.content = content

    @staticmethod
    def template_build(
        template_path: str | Path,
        content: dict[str, Any],
    ) -> str:
        """Render one template file and return its HTML body."""

        path = Path(template_path)
        if not path.is_absolute():
            path = BACKEND_ROOT / path

        if not path.is_file():
            raise FileNotFoundError(f"Template not found: {template_path}")

        environment = Environment(
            loader=FileSystemLoader(str(path.parent)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        rendered = environment.get_template(path.name).render(**content)
        return rendered

    def get_template(self) -> template:
        """Return the registered subject and rendered HTML for this type."""

        config = template_registery[self.type]
        template_path = Path(config["template_dir"]) / config["template"]
        body = self.template_build(template_path, self.content)
        return template(subject=config["subject"], body=body)


__all__ = [
    "TemplateManager",
    "TemplateType",
    "template",
    "template_registery",
]
