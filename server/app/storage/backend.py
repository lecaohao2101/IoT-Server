"""Key/value backing store for device state and conversation context.

Redis is the production backend: it is fast enough to sit on the voice path and it
lets several uvicorn workers share one view of the apartment. The in-memory backend
exists so the whole server boots and the test suite runs with no infrastructure --
it is explicitly refused when ``APP_ENV=prod``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol, runtime_checkable

from app.config import Settings

log = logging.getLogger(__name__)


@runtime_checkable
class KeyValueStore(Protocol):
    """The narrow slice of Redis this application actually uses."""

    async def get(self, key: str) -> str | None: ...
    async def mget(self, keys: list[str]) -> list[str | None]: ...
    async def set(self, key: str, value: str, ttl_s: int | None = None) -> None: ...
    async def delete(self, *keys: str) -> int: ...
    async def scan_prefix(self, prefix: str) -> list[str]: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...

    @property
    def kind(self) -> str: ...


class MemoryStore:
    """Process-local store with TTL support. Single-worker / development only."""

    def __init__(self, namespace: str = "sa") -> None:
        self._ns = namespace
        self._data: dict[str, tuple[str, float | None]] = {}
        self._lock = asyncio.Lock()

    @property
    def kind(self) -> str:
        return "memory"

    def _k(self, key: str) -> str:
        return f"{self._ns}:{key}"

    def _live(self, key: str, now: float) -> str | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires = entry
        if expires is not None and expires <= now:
            self._data.pop(key, None)
            return None
        return value

    async def get(self, key: str) -> str | None:
        async with self._lock:
            return self._live(self._k(key), time.monotonic())

    async def mget(self, keys: list[str]) -> list[str | None]:
        now = time.monotonic()
        async with self._lock:
            return [self._live(self._k(k), now) for k in keys]

    async def set(self, key: str, value: str, ttl_s: int | None = None) -> None:
        expires = time.monotonic() + ttl_s if ttl_s and ttl_s > 0 else None
        async with self._lock:
            self._data[self._k(key)] = (value, expires)

    async def delete(self, *keys: str) -> int:
        async with self._lock:
            return sum(1 for k in keys if self._data.pop(self._k(k), None) is not None)

    async def scan_prefix(self, prefix: str) -> list[str]:
        full = self._k(prefix)
        now = time.monotonic()
        async with self._lock:
            matches = [k for k in list(self._data) if k.startswith(full)]
            return [k[len(self._ns) + 1 :] for k in matches if self._live(k, now) is not None]

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        async with self._lock:
            self._data.clear()


class RedisStore:
    """Thin async Redis wrapper that namespaces every key."""

    def __init__(self, url: str, namespace: str = "sa", connect_timeout_s: float = 3.0) -> None:
        from redis.asyncio import Redis  # imported lazily so redis stays optional

        self._ns = namespace
        self._client = Redis.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=connect_timeout_s,
            socket_timeout=connect_timeout_s,
            health_check_interval=30,
            retry_on_timeout=True,
        )

    @property
    def kind(self) -> str:
        return "redis"

    @property
    def client(self):  # pragma: no cover - escape hatch for pub/sub
        return self._client

    def _k(self, key: str) -> str:
        return f"{self._ns}:{key}"

    async def get(self, key: str) -> str | None:
        return await self._client.get(self._k(key))

    async def mget(self, keys: list[str]) -> list[str | None]:
        if not keys:
            return []
        return await self._client.mget([self._k(k) for k in keys])

    async def set(self, key: str, value: str, ttl_s: int | None = None) -> None:
        if ttl_s and ttl_s > 0:
            await self._client.set(self._k(key), value, ex=ttl_s)
        else:
            await self._client.set(self._k(key), value)

    async def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        return int(await self._client.delete(*[self._k(k) for k in keys]))

    async def scan_prefix(self, prefix: str) -> list[str]:
        pattern = f"{self._k(prefix)}*"
        found: list[str] = []
        cursor = 0
        while True:
            cursor, batch = await self._client.scan(cursor=cursor, match=pattern, count=500)
            found.extend(k[len(self._ns) + 1 :] for k in batch)
            if cursor == 0:
                return found

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception as exc:  # noqa: BLE001 - health probe must not raise
            log.warning("redis ping failed", extra={"error": str(exc)})
            return False

    async def close(self) -> None:
        await self._client.aclose()


async def create_store(settings: Settings) -> KeyValueStore:
    """Build the configured store, falling back to memory only outside production."""
    if not settings.redis_url:
        log.warning("no REDIS_URL configured -- using in-memory state store (dev only)")
        return MemoryStore(settings.redis_namespace)

    store = RedisStore(
        settings.redis_url,
        namespace=settings.redis_namespace,
        connect_timeout_s=settings.redis_connect_timeout_s,
    )
    if await store.ping():
        log.info("connected to redis", extra={"url": _redact(settings.redis_url)})
        return store

    await store.close()
    if settings.app_env == "prod":
        from app.core.errors import ConfigError

        raise ConfigError("Redis is unreachable and APP_ENV=prod forbids the memory store")
    log.warning("redis unreachable -- falling back to in-memory state store")
    return MemoryStore(settings.redis_namespace)


def _redact(url: str) -> str:
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    return f"{scheme}://***@{rest.rpartition('@')[2]}"
