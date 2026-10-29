"""Shared pytest fixtures."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.base_config import Settings
from app.main import create_app
from tests.fakes import FakeRedis
from tests.helpers import build_settings


@pytest.fixture
def settings() -> Settings:
    return build_settings()


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def app(settings: Settings, fake_redis: FakeRedis) -> FastAPI:
    return create_app(settings, redis_client=fake_redis)  # type: ignore[arg-type]


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
