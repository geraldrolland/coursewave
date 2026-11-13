"""Google OAuth models and HTTP helpers for instructor authentication."""

from typing import Any

import httpx
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    ValidationError,
    field_validator,
)

from app.base_config import Settings

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


async def exchange_code_for_access_token(
    code: str,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Exchange a Google authorization code for an access token."""

    payload = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": settings.google_redirect_uri,
        "grant_type": "authorization_code",
    }

    try:
        if client is None:
            async with httpx.AsyncClient(
                timeout=GOOGLE_HTTP_TIMEOUT_SECONDS
            ) as http_client:
                response = await http_client.post(
                    GOOGLE_TOKEN_URL,
                    data=payload,
                )
        else:
            response = await client.post(GOOGLE_TOKEN_URL, data=payload)

        response.raise_for_status()
        body: Any = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GoogleAuthError("Google token exchange failed") from exc

    access_token = (
        body.get("access_token") if isinstance(body, dict) else None
    )
    if not isinstance(access_token, str) or not access_token:
        raise GoogleAuthError(
            "Google token response did not include an access token"
        )

    return access_token


async def fetch_google_user_info(
    access_token: str,
    client: httpx.AsyncClient | None = None,
) -> GoogleUserInfo:
    """Fetch the Google account details for an access token."""

    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        if client is None:
            async with httpx.AsyncClient(
                timeout=GOOGLE_HTTP_TIMEOUT_SECONDS
            ) as http_client:
                response = await http_client.get(
                    GOOGLE_USERINFO_URL,
                    headers=headers,
                )
        else:
            response = await client.get(
                GOOGLE_USERINFO_URL,
                headers=headers,
            )

        response.raise_for_status()
        body: Any = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GoogleAuthError("Google userinfo request failed") from exc

    try:
        return GoogleUserInfo.model_validate(body)
    except ValidationError as exc:
        raise GoogleAuthError("Google userinfo response is invalid") from exc


__all__ = [
    "GOOGLE_HTTP_TIMEOUT_SECONDS",
    "GOOGLE_TOKEN_URL",
    "GOOGLE_USERINFO_URL",
    "GoogleAuthError",
    "GoogleUserInfo",
    "exchange_code_for_access_token",
    "fetch_google_user_info",
]
