"""SendGrid email notification provider."""

import json
import logging
from typing import TYPE_CHECKING

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.base_config import get_settings
from app.services.notification.exception import (
    ProviderError,
    ProviderServiceNotAvailableError,
)
from app.services.notification.provider.base import BaseProvider
from app.services.notification.template_manager import TemplateManager

if TYPE_CHECKING:
    from app.services.notification.model import Notification

logger = logging.getLogger("notification.provider.email")

EMAIL_SUBJECT_REGISTRY = {
    "email_verification": "verify your email",
    "otp": "your verification code",
}


class EmailProvider(BaseProvider):
    """Deliver email notifications through SendGrid."""

    def __init__(
        self,
        api_key: str | None = None,
        from_email: str | None = None,
        from_name: str | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.sendgrid_api_key
        self.from_email = from_email or settings.sendgrid_from_email
        self.from_name = from_name or settings.sendgrid_from_name

    def process(self, notification: "Notification") -> None:
        """Render the email template and send it through SendGrid."""

        try:
            payload = notification.payload
            try:
                template_type = payload["template_type"]
                content = payload["content"]
                recipient = payload.get("to_email") or payload.get("email")
            except (AttributeError, KeyError, TypeError) as exc:
                raise ProviderError(
                    "Email notification payload is invalid"
                ) from exc

            if not recipient:
                raise ProviderError("Email recipient is missing")

            subject = EMAIL_SUBJECT_REGISTRY.get(template_type)
            if subject is None:
                raise ProviderError(
                    f"No email subject is registered for {template_type}"
                )

            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except json.JSONDecodeError as exc:
                    raise ProviderError(
                        "Email notification content is invalid"
                    ) from exc

            if not isinstance(content, dict):
                raise ProviderError("Email notification content is invalid")

            rendered = TemplateManager(template_type, content).get_template()
            message = Mail(
                from_email=(self.from_email, self.from_name),
                to_emails=recipient,
                subject=subject,
                html_content=rendered.body,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("Email notification processing failed") from exc

        try:
            response = SendGridAPIClient(self.api_key).send(message)
        except Exception as exc:
            raise ProviderServiceNotAvailableError(
                "Email provider service is unavailable"
            ) from exc

        try:
            status_code = response.status_code
            is_success = 200 <= status_code < 300
        except (AttributeError, TypeError) as exc:
            raise ProviderServiceNotAvailableError(
                "Email provider service returned an invalid response"
            ) from exc

        if not is_success:
            raise ProviderServiceNotAvailableError(
                f"Email provider service returned status {status_code}"
            )

        logger.info(
            "Email notification %s sent with template %s",
            notification.notification_id,
            template_type,
        )


__all__ = ["EMAIL_SUBJECT_REGISTRY", "EmailProvider"]
