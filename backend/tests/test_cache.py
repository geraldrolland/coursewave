"""Tests for the Redis GET cache middleware."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.responses import (
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastapi.testclient import TestClient

from app.base_config import Settings
from app.main import create_app
from app.middleware.cache import CACHE_ROUTES, MAX_CACHE_BODY_BYTES
from tests.fakes import FakeRedis
from tests.helpers import access_token, auth_headers, build_settings, seed_session

CACHE_PREFIX = "/student/api/v1"
CACHE_HEALTH_PATH = f"{CACHE_PREFIX}/health"
CACHE_TEST_PATH_NAMES = (
    "/health",
    "/no-store",
    "/private",
    "/vary",
    "/cookie",
    "/large",
    "/error",
    "/stream",
    "/text",
    "/problem",
    "/json-charset",
    "/json-stream",
    "/post",
)
TEST_CACHE_SETTINGS = {
    "cache_exclude_paths": "",
    "auth_exclude_paths": ",".join(
        [
            "/health",
            *(f"{CACHE_PREFIX}{path}" for path in CACHE_TEST_PATH_NAMES),
        ]
    ),
}


@contextmanager
def cache_client(
    settings: Settings,
    configure: Callable[[FastAPI], None] | None = None,
) -> Iterator[tuple[TestClient, FakeRedis]]:
    redis = FakeRedis()
    app = create_app(settings, redis_client=redis)  # type: ignore[arg-type]

    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_api_route(CACHE_HEALTH_PATH, health, methods=["GET"])
    if configure is not None:
        configure(app)
    with TestClient(app) as client:
        yield client, redis


def test_cache_routes_cover_student_and_instructor_apis() -> None:
    assert CACHE_ROUTES == (
        "/student/api/v1",
        "/instructor/api/v1",
    )


def test_public_get_is_cached_for_420_seconds() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)

    with cache_client(settings) as (client, redis):
        first = client.get(CACHE_HEALTH_PATH)
        second = client.get(CACHE_HEALTH_PATH)

    assert first.status_code == 200
    assert first.headers["x-cache"] == "MISS"
    assert second.status_code == 200
    assert second.headers["x-cache"] == "HIT"
    assert second.json() == {"status": "ok"}
    assert f"cache:{CACHE_HEALTH_PATH}" in redis.values
    assert redis.ttls[f"cache:{CACHE_HEALTH_PATH}"] == 420


def test_path_outside_cache_routes_is_not_cached() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)

    with cache_client(settings) as (client, redis):
        first = client.get("/health")
        second = client.get("/health")

    assert first.status_code == 200
    assert second.status_code == 200
    assert "x-cache" not in first.headers
    assert "x-cache" not in second.headers
    assert "cache:/health" not in redis.values


def test_cache_route_requires_a_path_segment_boundary() -> None:
    path = "/student/api/v10/value"
    settings = build_settings(
        cache_exclude_paths="",
        auth_exclude_paths=path,
    )

    def configure(app: FastAPI) -> None:
        async def value() -> dict[str, bool]:
            return {"ok": True}

        app.add_api_route(path, value, methods=["GET"])

    with cache_client(settings, configure) as (client, redis):
        response = client.get(path)

    assert response.status_code == 200
    assert "x-cache" not in response.headers
    assert f"cache:{path}" not in redis.values


def test_instructor_route_is_cached() -> None:
    path = "/instructor/api/v1/value"
    settings = build_settings(
        cache_exclude_paths="",
        auth_exclude_paths=path,
    )

    def configure(app: FastAPI) -> None:
        async def value() -> dict[str, bool]:
            return {"ok": True}

        app.add_api_route(path, value, methods=["GET"])

    with cache_client(settings, configure) as (client, redis):
        first = client.get(path)
        second = client.get(path)

    assert first.headers["x-cache"] == "MISS"
    assert second.headers["x-cache"] == "HIT"
    assert f"cache:{path}" in redis.values


def test_non_json_response_is_not_cached() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)
    path = f"{CACHE_PREFIX}/text"

    def configure(app: FastAPI) -> None:
        async def text() -> PlainTextResponse:
            return PlainTextResponse('{"ok": true}')

        app.add_api_route(path, text, methods=["GET"])

    with cache_client(settings, configure) as (client, redis):
        first = client.get(path)
        second = client.get(path)

    assert first.status_code == 200
    assert first.headers["x-cache"] == "MISS"
    assert second.headers["x-cache"] == "MISS"
    assert f"cache:{path}" not in redis.values


def test_json_with_suffix_is_not_cached() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)
    path = f"{CACHE_PREFIX}/problem"

    def configure(app: FastAPI) -> None:
        async def problem() -> Response:
            return Response(
                content='{"detail": "problem"}',
                media_type="application/problem+json",
            )

        app.add_api_route(path, problem, methods=["GET"])

    with cache_client(settings, configure) as (client, redis):
        first = client.get(path)
        second = client.get(path)

    assert first.status_code == 200
    assert second.headers["x-cache"] == "MISS"
    assert f"cache:{path}" not in redis.values


def test_json_with_charset_is_cached() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)
    path = f"{CACHE_PREFIX}/json-charset"

    def configure(app: FastAPI) -> None:
        async def charset() -> Response:
            return Response(
                content='{"ok": true}',
                headers={"content-type": "application/json; charset=utf-8"},
            )

        app.add_api_route(path, charset, methods=["GET"])

    with cache_client(settings, configure) as (client, redis):
        first = client.get(path)
        second = client.get(path)

    assert first.status_code == 200
    assert first.headers["x-cache"] == "MISS"
    assert second.headers["x-cache"] == "HIT"
    assert second.json() == {"ok": True}
    assert f"cache:{path}" in redis.values


def test_cache_key_includes_query_string() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)

    with cache_client(settings) as (client, _):
        first = client.get(f"{CACHE_HEALTH_PATH}?limit=10")
        second = client.get(f"{CACHE_HEALTH_PATH}?limit=20")
        repeat = client.get(f"{CACHE_HEALTH_PATH}?limit=10")

    assert first.headers["x-cache"] == "MISS"
    assert second.headers["x-cache"] == "MISS"
    assert repeat.headers["x-cache"] == "HIT"


def test_post_requests_are_not_cached() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)
    path = f"{CACHE_PREFIX}/post"

    def configure(app: FastAPI) -> None:
        async def post() -> dict[str, bool]:
            return {"ok": True}

        app.add_api_route(path, post, methods=["POST"])

    with cache_client(settings, configure) as (client, redis):
        response = client.post(path)

    assert response.status_code == 200
    assert "x-cache" not in response.headers
    assert not any(key.startswith("cache:") for key in redis.values)


def test_rejected_protected_get_is_not_cached() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)
    path = f"{CACHE_PREFIX}/protected"

    def configure(app: FastAPI) -> None:
        async def protected() -> dict[str, bool]:
            return {"ok": True}

        app.add_api_route(path, protected, methods=["GET"])

    with cache_client(settings, configure) as (client, redis):
        response = client.get(
            path,
            headers=auth_headers(access_token()),
        )

    assert response.status_code == 401
    assert "x-cache" not in response.headers
    assert f"cache:{path}" not in redis.values


def test_request_with_authorization_header_bypasses_cache() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)

    with cache_client(settings) as (client, redis):
        response = client.get(
            CACHE_HEALTH_PATH,
            headers={"Authorization": "Bearer irrelevant"},
        )

    assert response.status_code == 200
    assert "x-cache" not in response.headers
    assert f"cache:{CACHE_HEALTH_PATH}" not in redis.values


def test_request_with_cookie_bypasses_cache() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)

    with cache_client(settings) as (client, redis):
        response = client.get(
            CACHE_HEALTH_PATH,
            headers={"Cookie": "session=abc"},
        )

    assert response.status_code == 200
    assert "x-cache" not in response.headers
    assert f"cache:{CACHE_HEALTH_PATH}" not in redis.values


def test_authenticated_session_bypasses_cache() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)
    path = f"{CACHE_PREFIX}/protected"

    def configure(app: FastAPI) -> None:
        async def protected() -> dict[str, bool]:
            return {"ok": True}

        app.add_api_route(path, protected, methods=["GET"])

    with cache_client(settings, configure) as (client, redis):
        seed_session(redis)
        response = client.get(
            path,
            headers=auth_headers(access_token()),
        )

    assert response.status_code == 200
    assert "x-cache" not in response.headers
    assert f"cache:{path}" not in redis.values


def test_non_200_response_is_not_cached() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)
    path = f"{CACHE_PREFIX}/error"

    def configure(app: FastAPI) -> None:
        async def error() -> JSONResponse:
            return JSONResponse({"detail": "Not found"}, status_code=404)

        app.add_api_route(path, error, methods=["GET"])

    with cache_client(settings, configure) as (client, redis):
        response = client.get(path)

    assert response.status_code == 404
    assert response.headers["x-cache"] == "MISS"
    assert f"cache:{path}" not in redis.values


@pytest.mark.parametrize(
    ("path", "headers"),
    [
        (f"{CACHE_PREFIX}/no-store", {"Cache-Control": "no-store"}),
        (f"{CACHE_PREFIX}/private", {"Cache-Control": "private, max-age=60"}),
        (f"{CACHE_PREFIX}/vary", {"Vary": "*"}),
        (f"{CACHE_PREFIX}/cookie", {"Set-Cookie": "session=abc; Path=/"}),
    ],
)
def test_uncacheable_responses_are_not_stored(
    path: str,
    headers: dict[str, str],
) -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)

    def configure(app: FastAPI) -> None:
        async def endpoint() -> JSONResponse:
            return JSONResponse({"ok": True}, headers=headers)

        app.add_api_route(path, endpoint, methods=["GET"])

    with cache_client(settings, configure) as (client, redis):
        first = client.get(path)
        client.cookies.clear()
        second = client.get(path)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.headers["x-cache"] == "MISS"
    assert f"cache:{path}" not in redis.values


def test_large_response_is_not_cached() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)
    path = f"{CACHE_PREFIX}/large"

    def configure(app: FastAPI) -> None:
        async def large() -> PlainTextResponse:
            return PlainTextResponse("x" * (MAX_CACHE_BODY_BYTES + 1))

        app.add_api_route(path, large, methods=["GET"])

    with cache_client(settings, configure) as (client, redis):
        first = client.get(path)
        second = client.get(path)

    assert first.status_code == 200
    assert len(first.content) == MAX_CACHE_BODY_BYTES + 1
    assert second.headers["x-cache"] == "MISS"
    assert f"cache:{path}" not in redis.values


def test_cache_hit_does_not_reuse_cors_headers() -> None:
    settings = build_settings(
        **TEST_CACHE_SETTINGS,
        cors_origin="https://allowed.example",
    )

    with cache_client(settings) as (client, _):
        allowed = client.get(
            CACHE_HEALTH_PATH,
            headers={"Origin": "https://allowed.example"},
        )
        disallowed = client.get(
            CACHE_HEALTH_PATH,
            headers={"Origin": "https://evil.example"},
        )

    assert allowed.headers["access-control-allow-origin"] == (
        "https://allowed.example"
    )
    assert disallowed.headers["x-cache"] == "HIT"
    assert "access-control-allow-origin" not in disallowed.headers


def test_cache_hit_uses_the_current_request_id() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)

    with cache_client(settings) as (client, _):
        first = client.get(
            CACHE_HEALTH_PATH,
            headers={"x-request-id": "first-request"},
        )
        second = client.get(
            CACHE_HEALTH_PATH,
            headers={"x-request-id": "second-request"},
        )

    assert first.headers["x-request-id"] == "first-request"
    assert second.headers["x-cache"] == "HIT"
    assert second.headers["x-request-id"] == "second-request"


def test_streaming_response_is_not_cached() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)
    path = f"{CACHE_PREFIX}/stream"

    def configure(app: FastAPI) -> None:
        async def stream_body():
            yield b"one"
            yield b"two"

        async def stream() -> StreamingResponse:
            return StreamingResponse(stream_body(), media_type="text/plain")

        app.add_api_route(path, stream, methods=["GET"])

    with cache_client(settings, configure) as (client, redis):
        first = client.get(path)
        second = client.get(path)

    assert first.status_code == 200
    assert first.content == b"onetwo"
    assert second.headers["x-cache"] == "MISS"
    assert f"cache:{path}" not in redis.values


def test_multi_chunk_json_stream_is_not_cached() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)
    path = f"{CACHE_PREFIX}/json-stream"

    def configure(app: FastAPI) -> None:
        async def stream_body():
            yield b'{"ok":'
            yield b"true}"

        async def stream() -> StreamingResponse:
            return StreamingResponse(
                stream_body(),
                media_type="application/json",
            )

        app.add_api_route(path, stream, methods=["GET"])

    with cache_client(settings, configure) as (client, redis):
        first = client.get(path)
        second = client.get(path)

    assert first.status_code == 200
    assert first.content == b'{"ok":true}'
    assert first.headers["x-cache"] == "MISS"
    assert second.status_code == 200
    assert second.content == b'{"ok":true}'
    assert second.headers["x-cache"] == "MISS"
    assert f"cache:{path}" not in redis.values


def test_redis_read_failure_fails_open() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)

    with cache_client(settings) as (client, redis):
        redis.fail("get")
        response = client.get(CACHE_HEALTH_PATH)

    assert response.status_code == 200
    assert response.headers["x-cache"] == "MISS"


def test_redis_write_failure_fails_open() -> None:
    settings = build_settings(**TEST_CACHE_SETTINGS)

    with cache_client(settings) as (client, redis):
        redis.fail("set")
        response = client.get(CACHE_HEALTH_PATH)

    assert response.status_code == 200
    assert f"cache:{CACHE_HEALTH_PATH}" not in redis.values
