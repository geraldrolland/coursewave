"""JSON logging configuration for the student service."""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Configure the service logger with a JSON console handler."""

    logger = logging.getLogger("backend")
    logger.setLevel(level.upper())
    logger.propagate = False

    has_handler = any(
        getattr(handler, "_backend_handler", False)
        for handler in logger.handlers
    )
    if not has_handler:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        handler._backend_handler = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
