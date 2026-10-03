"""Token-bucket rate limiting.

Used in two places: to stop a stuck firmware loop from hammering the LLM, and to
stop the safety validator from letting a relay be toggled at a rate that would
physically wear it out.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class TokenBucket:
    capacity: float
    refill_per_second: float
    _tokens: float = field(default=0.0, init=False)
    _updated: float = field(default_factory=time.monotonic, init=False)

    def __post_init__(self) -> None:
        self._tokens = float(self.capacity)

    def _refill(self, now: float) -> None:
        elapsed = now - self._updated
        if elapsed < 0:
            # An injected or rewound clock: re-base rather than refusing forever.
            self._updated = now
            return
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_second)
            self._updated = now

    def try_consume(self, amount: float = 1.0, now: float | None = None) -> bool:
        self._refill(now if now is not None else time.monotonic())
        if self._tokens >= amount:
            self._tokens -= amount
            return True
        return False

    def retry_after(self, amount: float = 1.0, now: float | None = None) -> float:
        """Seconds until ``amount`` tokens would be available."""
        self._refill(now if now is not None else time.monotonic())
        if self._tokens >= amount or self.refill_per_second <= 0:
            return 0.0
        return (amount - self._tokens) / self.refill_per_second

    @property
    def tokens(self) -> float:
        return self._tokens


class KeyedRateLimiter:
    """One bucket per key (per device, per session, ...), created on first use."""

    def __init__(self, capacity: float, refill_per_second: float, max_keys: int = 4096) -> None:
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.max_keys = max_keys
        self._buckets: dict[str, TokenBucket] = {}

    def _bucket(self, key: str) -> TokenBucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= self.max_keys:
                self._evict_full()
            bucket = TokenBucket(self.capacity, self.refill_per_second)
            self._buckets[key] = bucket
        return bucket

    def _evict_full(self) -> None:
        """Drop buckets that have fully refilled -- they carry no state worth keeping."""
        now = time.monotonic()
        stale = [k for k, b in self._buckets.items() if b.try_consume(0.0, now) and b.tokens >= b.capacity]
        for key in stale[: max(1, len(stale))]:
            self._buckets.pop(key, None)
        if not stale:
            self._buckets.pop(next(iter(self._buckets)), None)

    def allow(self, key: str, amount: float = 1.0) -> bool:
        return self._bucket(key).try_consume(amount)

    def retry_after(self, key: str, amount: float = 1.0) -> float:
        return self._bucket(key).retry_after(amount)

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._buckets.clear()
        else:
            self._buckets.pop(key, None)
