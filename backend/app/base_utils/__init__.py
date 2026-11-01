"""Shared utility helpers."""

from datetime import datetime, timezone

__all__ = ["utc_now"]


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""

    return datetime.now(timezone.utc)
