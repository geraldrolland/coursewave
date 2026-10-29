"""Tests for CORS behavior."""

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.base_config import Settings
from app.main import create_app
from tests.fakes import FakeRedis
from tests.helpers import build_settings


@contextmanager
def cors_client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, redis_client=FakeRedis())  # type: ignore[arg-type]
    with TestClient(app) as client:
        yield client


def test_simple_request_allows_configured_origin(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": "https://app.example"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_preflight_is_handled_before_authentication(client: TestClient) -> None:
    response = client.options(
        "/student/api/v1/register",
        headers={
            "Origin": "https://app.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, Authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_expose_headers_include_middleware_headers(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": "https://app.example"})

    expose_headers = response.headers["access-control-expose-headers"].lower()
    assert "x-request-id" in expose_headers
    assert "x-ratelimit-limit" in expose_headers
    assert "retry-after" in expose_headers
    assert "x-cache" in expose_headers


def test_disallowed_origin_gets_no_cors_header() -> None:
    settings = build_settings(cors_origin="https://allowed.example")

    with cors_client(settings) as client:
        response = client.get(
            "/health",
            headers={"Origin": "https://evil.example"},
        )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_allowed_origin_and_credentials_are_echoed() -> None:
    settings = build_settings(
        cors_origin="https://allowed.example",
        cors_credentials=True,
    )

    with cors_client(settings) as client:
        response = client.get(
            "/health",
            headers={"Origin": "https://allowed.example"},
        )

    assert response.status_code == 200
    assert (
        response.headers["access-control-allow-origin"]
        == "https://allowed.example"
    )
    assert response.headers["access-control-allow-credentials"] == "true"
