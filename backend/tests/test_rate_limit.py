"""Tests for Redis rate limiting."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.base_config import Settings
from app.main import create_app
from app.middleware.ratelimit import parse_ratelimit_routes
from app.middleware.ratelimit.ratelimiter_registry import RATELIMITER_REGISTRY
from tests.fakes import FakeRedis
from tests.helpers import access_token, auth_headers, build_settings, seed_session

WILDCARD_ROUTE = {
    "prefix": "*",
    "window_ms": 90000,
    "strategy": "fixed_window",
}


@contextmanager
def rate_client(
    settings: Settings,
    configure: Callable[[FastAPI], None] | None = None,
) -> Iterator[tuple[TestClient, FakeRedis]]:
    redis = FakeRedis()
    app = create_app(settings, redis_client=redis)  # type: ignore[arg-type]
    if configure is not None:
        configure(app)
    with TestClient(app) as client:
        yield client, redis


def test_registry_maps_strategy_names_to_handlers() -> None:
    assert set(RATELIMITER_REGISTRY) == {"fixed_window", "token_bucket"}
    assert all(callable(handler) for handler in RATELIMITER_REGISTRY.values())


def test_parse_ratelimit_routes_normalizes_and_ignores_malformed_entries() -> None:
    routes = parse_ratelimit_routes(
        [
            {
                "prefix": "/student/api/",
                "window_ms": 90000,
                "strategy": "fixedWindow",
            },
            {
                "prefix": "student/other",
                "window_ms": "5000",
                "strategy": "tokenBucket",
            },
            "not-a-dict",
            {"prefix": "", "window_ms": 1000, "strategy": "fixed_window"},
            {"prefix": "/broken", "window_ms": "not-a-number"},
        ]
    )

    by_prefix = {route.prefix: route for route in routes}
    assert by_prefix["/student/api"].strategy == "fixed_window"
    assert by_prefix["/student/api"].window_ms == 90000
    assert by_prefix["/student/other"].strategy == "token_bucket"
    assert by_prefix["/student/other"].window_ms == 5000
    assert len(routes) == 2


def test_parse_ratelimit_routes_handles_non_lists_and_unknown_strategies() -> None:
    assert parse_ratelimit_routes(None) == []
    assert parse_ratelimit_routes("not-a-list") == []

    routes = parse_ratelimit_routes(
        [{"prefix": "/health", "strategy": "sliding_window"}]
    )

    assert len(routes) == 1
    assert routes[0].strategy == "fixed_window"
    assert routes[0].window_ms == 90000


def test_fixed_window_limits_requests_and_sets_headers() -> None:
    settings = build_settings(
        rate_limit_max=2,
        rate_limit_exclude_paths="",
        cache_enabled=False,
        ratelimit_routes=[WILDCARD_ROUTE],
    )

    with rate_client(settings) as (client, redis):
        first = client.get("/health")
        second = client.get("/health")
        third = client.get("/health")

    assert first.status_code == 200
    assert first.headers["x-ratelimit-limit"] == "2"
    assert first.headers["x-ratelimit-remaining"] == "1"
    assert int(first.headers["x-ratelimit-reset"]) > 0

    assert second.status_code == 200
    assert second.headers["x-ratelimit-remaining"] == "0"

    assert third.status_code == 429
    assert third.headers["x-ratelimit-remaining"] == "0"
    assert int(third.headers["retry-after"]) > 0
    assert third.json() == {
        "detail": "Too many requests, please try again later"
    }
    assert any(
        key.startswith("ratelimit:fixed_window:*:")
        for key in redis.values
    )


def test_authenticated_users_and_anonymous_ips_have_separate_buckets() -> None:
    settings = build_settings(
        rate_limit_max=1,
        rate_limit_exclude_paths="",
        cache_enabled=False,
        ratelimit_routes=[WILDCARD_ROUTE],
    )

    with rate_client(settings) as (client, redis):
        seed_session(redis)
        headers = auth_headers(access_token())

        first = client.get("/openapi.json", headers=headers)
        second = client.get("/openapi.json", headers=headers)
        anonymous = client.get("/health")

    assert first.status_code == 200
    assert second.status_code == 429
    assert anonymous.status_code == 200

    keys = [
        key
        for key in redis.values
        if key.startswith("ratelimit:fixed_window:*:")
    ]
    assert any(key.endswith(":user-123") for key in keys)
    assert len(keys) == 2


def test_token_bucket_allows_burst_then_rejects() -> None:
    settings = build_settings(
        rate_limit_max=2,
        rate_limit_exclude_paths="",
        cache_enabled=False,
        ratelimit_routes=[
            {
                "prefix": "*",
                "window_ms": 60000,
                "strategy": "token_bucket",
            }
        ],
    )

    with rate_client(settings) as (client, redis):
        first = client.get("/health")
        second = client.get("/health")
        third = client.get("/health")

    assert first.status_code == 200
    assert first.headers["x-ratelimit-limit"] == "2"
    assert second.status_code == 200
    assert third.status_code == 429
    assert int(third.headers["retry-after"]) >= 1
    assert any(
        key.startswith("ratelimit:token_bucket:*:")
        for key in redis.values
    )


def test_longest_prefix_route_wins() -> None:
    settings = build_settings(
        rate_limit_max=10,
        rate_limit_exclude_paths="",
        cache_enabled=False,
        auth_exclude_paths="/health,/health/deep",
        ratelimit_routes=[
            {
                "prefix": "/health",
                "window_ms": 90000,
                "strategy": "fixed_window",
            },
            {
                "prefix": "/health/deep",
                "window_ms": 60000,
                "strategy": "fixed_window",
            },
        ],
    )

    def configure(app: FastAPI) -> None:
        async def deep() -> dict[str, bool]:
            return {"ok": True}

        app.add_api_route("/health/deep", deep, methods=["GET"])

    with rate_client(settings, configure) as (client, redis):
        deep = client.get("/health/deep")
        shallow = client.get("/health")

    assert deep.status_code == 200
    assert deep.headers["x-ratelimit-reset"] == "60"
    assert shallow.status_code == 200
    assert shallow.headers["x-ratelimit-reset"] == "90"
    assert any(
        key.startswith("ratelimit:fixed_window:/health/deep:")
        for key in redis.values
    )
    assert any(
        key.startswith("ratelimit:fixed_window:/health:")
        for key in redis.values
    )


def test_wildcard_route_matches_unconfigured_paths() -> None:
    settings = build_settings(
        rate_limit_max=1,
        rate_limit_exclude_paths="",
        cache_enabled=False,
        ratelimit_routes=[
            {
                "prefix": "/student/api",
                "window_ms": 90000,
                "strategy": "fixed_window",
            },
            {
                "prefix": "*",
                "window_ms": 30000,
                "strategy": "fixed_window",
            },
        ],
    )

    with rate_client(settings) as (client, redis):
        first = client.get("/health")
        second = client.get("/health")

    assert first.status_code == 200
    assert first.headers["x-ratelimit-reset"] == "30"
    assert second.status_code == 429
    assert any(
        key.startswith("ratelimit:fixed_window:*:")
        for key in redis.values
    )


def test_builtin_fallback_is_used_without_a_wildcard_route() -> None:
    settings = build_settings(
        rate_limit_max=1,
        rate_limit_exclude_paths="",
        cache_enabled=False,
        ratelimit_routes=[],
    )

    with rate_client(settings) as (client, redis):
        first = client.get("/health")
        second = client.get("/health")

    assert first.status_code == 200
    assert first.headers["x-ratelimit-reset"] == "90"
    assert second.status_code == 429
    assert any(
        key.startswith("ratelimit:fixed_window:*:")
        for key in redis.values
    )


def test_health_is_excluded_by_default() -> None:
    settings = build_settings(rate_limit_max=1, cache_enabled=False)

    with rate_client(settings) as (client, redis):
        first = client.get("/health")
        second = client.get("/health")

    assert first.status_code == 200
    assert second.status_code == 200
    assert "x-ratelimit-limit" not in first.headers
    assert not any(key.startswith("ratelimit:") for key in redis.values)


def test_redis_failure_fails_open() -> None:
    settings = build_settings(
        rate_limit_max=1,
        rate_limit_exclude_paths="",
        cache_enabled=False,
        ratelimit_routes=[WILDCARD_ROUTE],
    )

    with rate_client(settings) as (client, redis):
        redis.fail("incr")
        response = client.get("/health")

    assert response.status_code == 200
    assert "x-ratelimit-limit" not in response.headers
