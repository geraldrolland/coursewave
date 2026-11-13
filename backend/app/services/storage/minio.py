"""MinIO-backed storage helpers for profile pictures."""

from io import BytesIO
from urllib.parse import urlparse
from uuid import uuid4

from minio import Minio

from app.base_config import Settings


def create_minio_client(settings: Settings) -> Minio:
    """Create the MinIO client shared by profile-picture requests."""

    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def build_profile_picture_object_key(extension: str) -> str:
    """Return a unique object key for a profile picture."""

    return f"{uuid4()}{extension.lower()}"


def build_profile_picture_url(settings: Settings, object_key: str) -> str:
    """Build the public URL for a stored profile picture."""

    base_url = settings.minio_public_url.rstrip("/")
    return f"{base_url}/{settings.minio_bucket}/{object_key}"


def object_key_from_profile_picture_url(
    url: str,
    bucket: str,
) -> str | None:
    """Extract an object key from a bucket URL, or return ``None``."""

    path = urlparse(url).path
    prefix = f"/{bucket}/"
    if not path.startswith(prefix):
        return None

    object_key = path[len(prefix):]
    return object_key or None


def upload_profile_picture(
    client: Minio,
    bucket: str,
    object_key: str,
    data: bytes,
    content_type: str,
) -> None:
    """Upload profile-picture bytes to the configured bucket."""

    client.put_object(
        bucket,
        object_key,
        BytesIO(data),
        length=len(data),
        content_type=content_type,
    )


def delete_profile_picture(
    client: Minio,
    bucket: str,
    object_key: str,
) -> None:
    """Delete a profile-picture object from the configured bucket."""

    client.remove_object(bucket, object_key)
