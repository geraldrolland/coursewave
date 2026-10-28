"""Tests for the shared database session dependency."""

import asyncio
from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.base_dependencies import get_session


class FakeSession:
    """Minimal async session used to exercise dependency cleanup."""

    def __init__(self) -> None:
        self.rolled_back = False
        self.closed = False

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> bool:
        self.closed = True
        return False

    async def rollback(self) -> None:
        self.rolled_back = True


def test_get_session_yields_async_session() -> None:
    async def consume() -> None:
        generator = get_session()
        try:
            session = await anext(generator)
            assert isinstance(session, AsyncSession)
        finally:
            await generator.aclose()

    asyncio.run(consume())


def test_get_session_rolls_back_and_closes_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = FakeSession()

    def fake_session_factory(**kwargs: Any) -> Any:
        assert kwargs["bind"] is not None
        assert kwargs["class_"] is AsyncSession
        assert kwargs["expire_on_commit"] is False
        return lambda: fake_session

    monkeypatch.setattr(
        "app.base_dependencies.async_sessionmaker",
        fake_session_factory,
    )

    async def consume() -> None:
        generator = get_session()
        await anext(generator)
        with pytest.raises(RuntimeError, match="boom"):
            await generator.athrow(RuntimeError("boom"))

    asyncio.run(consume())

    assert fake_session.rolled_back is True
    assert fake_session.closed is True
