"""Shared pytest fixtures."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.base_config import Settings
from app.main import create_app
from tests.helpers import TEST_PROXY_SECRET


@pytest.fixture
def settings() -> Settings:
    return Settings(
        proxy_secret=TEST_PROXY_SECRET,
        proxy_signature_ttl_seconds=30,
        log_level="CRITICAL",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
