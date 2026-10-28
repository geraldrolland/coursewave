"""Tests for the gateway proxy signature middleware."""

import time

import pytest
from fastapi.testclient import TestClient

from tests.helpers import sign_request, signed_headers


def test_health_is_exempt_from_signature(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_valid_signature_allows_request(client: TestClient) -> None:
    response = client.get("/openapi.json", headers=signed_headers("GET", "/openapi.json"))

    assert response.status_code == 200
    assert "openapi" in response.json()


def test_query_string_is_part_of_signature(client: TestClient) -> None:
    url = "/openapi.json?limit=10&offset=0"

    response = client.get(url, headers=signed_headers("GET", url))
    assert response.status_code == 200

    response = client.get(url, headers=signed_headers("GET", "/openapi.json"))
    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid proxy signature"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"x-proxy-signature": "deadbeef"},
        {"x-proxy-timestamp": str(int(time.time() * 1000))},
    ],
)
def test_missing_signature_headers_return_403(
    client: TestClient,
    headers: dict[str, str],
) -> None:
    response = client.get("/openapi.json", headers=headers)

    assert response.status_code == 403
    assert response.json() == {"detail": "Missing proxy signature headers"}


def test_invalid_timestamp_returns_403(client: TestClient) -> None:
    response = client.get(
        "/openapi.json",
        headers={
            "x-proxy-signature": "deadbeef",
            "x-proxy-timestamp": "not-a-number",
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid proxy timestamp"}


@pytest.mark.parametrize("offset_ms", [-31_000, 31_000])
def test_timestamp_outside_window_returns_403(
    client: TestClient,
    offset_ms: int,
) -> None:
    timestamp = int(time.time() * 1000) + offset_ms
    response = client.get(
        "/openapi.json",
        headers=signed_headers("GET", "/openapi.json", timestamp),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Proxy signature expired"}


def test_tampered_signature_returns_403(client: TestClient) -> None:
    headers = signed_headers("GET", "/openapi.json")
    headers["x-proxy-signature"] = "0" * 64

    response = client.get("/openapi.json", headers=headers)

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid proxy signature"}


def test_signature_with_wrong_secret_returns_403(client: TestClient) -> None:
    headers = signed_headers("GET", "/openapi.json", secret="wrong-secret")

    response = client.get("/openapi.json", headers=headers)

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid proxy signature"}


def test_signature_must_match_method(client: TestClient) -> None:
    timestamp = int(time.time() * 1000)
    response = client.get(
        "/openapi.json",
        headers={
            "x-proxy-signature": sign_request("POST", "/openapi.json", timestamp),
            "x-proxy-timestamp": str(timestamp),
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid proxy signature"}
