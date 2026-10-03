"""In-process publish/subscribe used to fan server-side events out to WebSockets.

Topics are dot-separated (``device.state.living_room_light``); subscribers match on
a prefix. Each subscriber owns a bounded queue -- a slow consumer loses its oldest
events instead of stalling the publisher.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.core.utils import new_id, utc_iso

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Event:
    topic: str
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: new_id("evt"))
    ts: str = field(default_factory=utc_iso)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "ts": self.ts, "topic": self.topic, **self.payload}


class _Subscription:
    __slots__ = ("prefixes", "queue", "dropped")

    def __init__(self, prefixes: tuple[str, ...], maxsize: int) -> None:
        self.prefixes = prefixes
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def matches(self, topic: str) -> bool:
        if not self.prefixes:
            return True
        return any(topic == p or topic.startswith(p + ".") for p in self.prefixes)

    def offer(self, event: Event) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):  # race only
                self.queue.get_nowait()  # shed the oldest
            self.dropped += 1
            self.queue.put_nowait(event)


class EventBus:
    """Fan-out hub. One instance per process, owned by the application container."""

    def __init__(self, queue_size: int = 256) -> None:
        self._subs: set[_Subscription] = set()
        self._queue_size = queue_size
        self._lock = asyncio.Lock()

    async def publish(self, topic: str, payload: dict[str, Any]) -> Event:
        event = Event(topic=topic, payload=payload)
        async with self._lock:
            targets = [s for s in self._subs if s.matches(topic)]
        for sub in targets:
            sub.offer(event)
        return event

    async def subscribe(self, *prefixes: str) -> AsyncIterator[Event]:
        """Usage: ``async for event in bus.subscribe("device.state"): ...``"""
        sub = _Subscription(tuple(prefixes), self._queue_size)
        async with self._lock:
            self._subs.add(sub)
        try:
            while True:
                yield await sub.queue.get()
        finally:
            async with self._lock:
                self._subs.discard(sub)
            if sub.dropped:
                log.warning("event subscriber dropped events", extra={"dropped": sub.dropped})

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


# -- canonical topics ---------------------------------------------------------

TOPIC_DEVICE_STATE = "device.state"
TOPIC_DEVICE_AVAILABILITY = "device.availability"
TOPIC_COMMAND_DISPATCHED = "command.dispatched"
TOPIC_COMMAND_REJECTED = "command.rejected"
TOPIC_CONVERSATION_TURN = "conversation.turn"
TOPIC_SYSTEM = "system"
