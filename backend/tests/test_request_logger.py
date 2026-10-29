"""Tests for the structured request logger middleware."""

import uuid
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.base_config import Settings
from app.middleware.auth import AuthMiddleware
from app.middleware.request_logger import RequestLoggerMiddleware
from tests.fakes import FakeRedis


class CapturingLogger:
    """Minimal logger stand-in that records emitted entries."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def info(self, message: str, extra: dict[str, Any] | None = None) -> None:
        self.entries.append({"message": message, "extra": extra or {}})


def _build_app(
    settings: Settings,
    logger: CapturingLogger | None = None,
    protect: bool = False,
) -> FastAPI:
    app = FastAPI()

    @app.get("/test")
    async def test_route() -> dict[str, bool]:
        return {"ok": True}

    if protect:
        redis = FakeRedis()
        app.add_middleware(
            AuthMiddleware,
            settings=settings,
            redis_provider=lambda: redis,
        )
    if logger is not None:
        app.add_middleware(RequestLoggerMiddleware, logger=logger)
    return app


def test_logs_successful_request(settings: Settings) -> None:
    logger = CapturingLogger()
    app = _build_app(settings, logger)

    with TestClient(app) as client:
        response = client.get("/test", headers={"x-request-id": "req-123"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "req-123"
    assert len(logger.entries) == 1

    entry = logger.entries[0]
    assert entry["message"] == "Request completed"
    fields = entry["extra"]["fields"]
    assert fields["method"] == "GET"
    assert fields["path"] == "/test"
    assert fields["status_code"] == 200
    assert fields["message"] == "Request completed"
    assert fields["request_id"] == "req-123"
    assert isinstance(fields["duration_ms"], float)


def test_generates_request_id_when_missing(settings: Settings) -> None:
    logger = CapturingLogger()
    app = _build_app(settings, logger)

    with TestClient(app) as client:
        response = client.get("/test")

    request_id = response.headers["x-request-id"]
    uuid.UUID(request_id)
    assert logger.entries[0]["extra"]["fields"]["request_id"] == request_id


def test_logs_auth_rejection(settings: Settings) -> None:
    logger = CapturingLogger()
    app = _build_app(settings, logger, protect=True)

    with TestClient(app) as client:
        response = client.get("/test", headers={"x-request-id": "req-401"})

    assert response.status_code == 401
    assert response.json() == {
        "detail": "Missing or invalid Authorization header"
    }

    entry = logger.entries[0]
    assert entry["message"] == "Request failed"
    fields = entry["extra"]["fields"]
    assert fields["method"] == "GET"
    assert fields["path"] == "/test"
    assert fields["status_code"] == 401
    assert fields["message"] == "Request failed"
    assert fields["request_id"] == "req-401"


def test_logs_not_found_as_failed(settings: Settings) -> None:
    logger = CapturingLogger()
    app = _build_app(settings, logger)

    with TestClient(app) as client:
        client.get("/missing", headers={"x-request-id": "req-404"})

    entry = logger.entries[0]
    assert entry["message"] == "Request failed"
    assert entry["extra"]["fields"]["status_code"] == 404
