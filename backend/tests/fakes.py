"""In-memory async Redis fake used by the test suite."""

from collections.abc import Iterable
from typing import Any

from redis.exceptions import RedisError


class FakeRedis:
    """Small in-memory subset of ``redis.asyncio.Redis``.

    Failures can be injected per operation, for example
    ``redis.fail("get")`` to simulate a Redis outage.
    """

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.token_buckets: dict[str, tuple[float, float]] = {}
        self.deleted: list[str] = []
        self.failures: dict[str, Exception] = {}
        self.fail_all: Exception | None = None
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def fail(self, operation: str, error: Exception | None = None) -> None:
        """Make the next calls to ``operation`` raise ``error``."""

        self.failures[operation] = error or RedisError("redis unavailable")

    def fail_everything(self, error: Exception | None = None) -> None:
        """Make every Redis operation raise ``error``."""

        self.fail_all = error or RedisError("redis unavailable")

    def seed(self, key: str, value: str, ttl: int | None = None) -> None:
        """Synchronously preload a key for a test."""

        self.values[key] = value
        if ttl is not None:
            self.ttls[key] = ttl

    def _record(self, operation: str, *args: Any) -> None:
        self.calls.append((operation, args))
        error = self.fail_all or self.failures.get(operation)
        if error is not None:
            raise error

    async def get(self, key: str) -> str | None:
        self._record("get", key)
        return self.values.get(key)

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        self._record("set", key, value)
        if nx and key in self.values:
            return None

        self.values[key] = str(value)
        if ex is not None:
            self.ttls[key] = int(ex)
        elif px is not None:
            self.ttls[key] = max(1, int(px) // 1000)
        else:
            self.ttls.pop(key, None)
        return True

    async def delete(self, *keys: str) -> int:
        self._record("delete", *keys)
        removed = 0
        for key in keys:
            self.deleted.append(key)
            if key in self.values:
                removed += 1
                del self.values[key]
            self.ttls.pop(key, None)
            self.token_buckets.pop(key, None)
        return removed

    async def incr(self, key: str) -> int:
        self._record("incr", key)
        current = int(self.values.get(key, "0")) + 1
        self.values[key] = str(current)
        return current

    async def expire(self, key: str, seconds: int) -> bool:
        self._record("expire", key, seconds)
        self.ttls[key] = int(seconds)
        return True

    async def ttl(self, key: str) -> int:
        self._record("ttl", key)
        return self.ttls.get(key, -1)

    async def eval(
        self,
        script: str,
        numkeys: int,
        *args: Any,
    ) -> list[int]:
        """Emulate the token-bucket Lua script used by the middleware."""

        self._record("eval", script, numkeys, *args)
        if numkeys != 1:
            raise RedisError("unsupported fake eval")

        key = str(args[0])
        capacity = float(args[1])
        refill_rate = float(args[2])
        now_ms = float(args[3])
        self.ttls[key] = max(1, int(args[4]) // 1000)
        self.values.setdefault(key, "hash")

        tokens, timestamp = self.token_buckets.get(key, (capacity, now_ms))
        elapsed_seconds = max(0.0, now_ms - timestamp) / 1000.0
        tokens = min(capacity, tokens + elapsed_seconds * refill_rate)

        allowed = 0
        if tokens >= 1:
            tokens -= 1
            allowed = 1

        self.token_buckets[key] = (tokens, now_ms)
        return [allowed, int(tokens * 1000)]

    async def aclose(self) -> None:
        return None


def delete_keys(redis: FakeRedis, keys: Iterable[str]) -> None:
    """Synchronously remove keys from a fake Redis instance."""

    for key in keys:
        redis.values.pop(key, None)
        redis.ttls.pop(key, None)
