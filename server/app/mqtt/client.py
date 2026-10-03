"""Managed MQTT connection.

aiomqtt gives us a context-managed client; this wraps it in a supervised task that
reconnects with exponential backoff and jitter, re-subscribes on every reconnect,
and hands inbound messages to a single async callback.

Publishing while the link is down raises :class:`TransportError` instead of
queueing. A command the user spoke five seconds ago is not worth replaying once the
broker returns -- by then they have usually pressed the switch themselves.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.config import Settings
from app.core.errors import TransportError
from app.mqtt.topics import OFFLINE, ONLINE, server_status_topic, subscription_filters

log = logging.getLogger(__name__)

MessageHandler = Callable[[str, bytes], Awaitable[None]]


class MqttTransport(Protocol):
    @property
    def connected(self) -> bool: ...
    async def start(self, handler: MessageHandler) -> None: ...
    async def stop(self) -> None: ...
    async def publish(
        self, topic: str, payload: bytes, *, qos: int | None = None, retain: bool = False
    ) -> None: ...
    def stats(self) -> dict[str, Any]: ...


class MqttClient:
    """Supervised aiomqtt connection."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._handler: MessageHandler | None = None
        self._task: asyncio.Task[None] | None = None
        self._client: Any = None
        self._connected = asyncio.Event()
        self._stopping = False
        self._published = 0
        self._received = 0
        self._reconnects = 0
        self._last_error: str | None = None

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def start(self, handler: MessageHandler) -> None:
        self._handler = handler
        self._stopping = False
        self._task = asyncio.create_task(self._supervise(), name="mqtt-supervisor")
        # Give the first connection a moment so startup logs tell the truth,
        # but never block boot on a broker that is down.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._connected.wait(), timeout=3.0)

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._client = None
        self._connected.clear()

    async def publish(
        self, topic: str, payload: bytes, *, qos: int | None = None, retain: bool = False
    ) -> None:
        if not self.connected or self._client is None:
            raise TransportError(
                "MQTT broker is not connected", details={"topic": topic, "last_error": self._last_error}
            )
        try:
            await self._client.publish(
                topic, payload=payload, qos=qos if qos is not None else self._s.mqtt_qos, retain=retain
            )
        except Exception as exc:  # noqa: BLE001 - aiomqtt raises several types
            self._connected.clear()
            raise TransportError(f"MQTT publish failed: {exc}", details={"topic": topic}) from exc
        self._published += 1

    async def wait_connected(self, timeout: float = 5.0) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    def stats(self) -> dict[str, Any]:
        return {
            "kind": "mqtt",
            "connected": self.connected,
            "host": f"{self._s.mqtt_host}:{self._s.mqtt_port}",
            "published": self._published,
            "received": self._received,
            "reconnects": self._reconnects,
            "last_error": self._last_error,
        }

    # ---------------------------------------------------------------- loop
    async def _supervise(self) -> None:
        backoff = self._s.mqtt_reconnect_min_s
        while not self._stopping:
            try:
                await self._run_once()
                backoff = self._s.mqtt_reconnect_min_s
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure means: reconnect
                self._last_error = str(exc)
                self._connected.clear()
                self._client = None
                self._reconnects += 1
                delay = min(backoff, self._s.mqtt_reconnect_max_s) * (0.7 + 0.6 * random.random())
                log.warning(
                    "mqtt disconnected, retrying",
                    extra={"error": str(exc), "retry_in_s": round(delay, 2)},
                )
                await asyncio.sleep(delay)
                backoff = min(backoff * 2, self._s.mqtt_reconnect_max_s)

    async def _run_once(self) -> None:
        import aiomqtt

        status_topic = server_status_topic(self._s.mqtt_base_topic)
        kwargs: dict[str, Any] = {
            "hostname": self._s.mqtt_host,
            "port": self._s.mqtt_port,
            "identifier": self._s.mqtt_client_id,
            "keepalive": self._s.mqtt_keepalive_s,
            "will": aiomqtt.Will(
                topic=status_topic, payload=OFFLINE.encode(), qos=1, retain=True
            ),
        }
        if self._s.mqtt_username:
            kwargs["username"] = self._s.mqtt_username
            kwargs["password"] = (
                self._s.mqtt_password.get_secret_value() if self._s.mqtt_password else None
            )
        if self._s.mqtt_tls:
            kwargs["tls_params"] = aiomqtt.TLSParameters()

        async with aiomqtt.Client(**kwargs) as client:
            self._client = client
            self._last_error = None
            for topic_filter in subscription_filters(self._s.mqtt_base_topic):
                await client.subscribe(topic_filter, qos=self._s.mqtt_qos)
            await client.publish(status_topic, ONLINE.encode(), qos=1, retain=True)
            self._connected.set()
            log.info(
                "mqtt connected",
                extra={"host": self._s.mqtt_host, "port": self._s.mqtt_port},
            )

            async for message in client.messages:
                self._received += 1
                if self._handler is None:
                    continue
                topic = str(message.topic)
                payload = message.payload if isinstance(message.payload, bytes) else bytes(
                    message.payload or b""
                )
                try:
                    await self._handler(topic, payload)
                except Exception:  # noqa: BLE001 - one bad message must not kill the link
                    log.exception("mqtt message handler failed", extra={"topic": topic})


class LoopbackMqtt:
    """Broker-free transport for demos and tests.

    Commands published to ``.../set`` are echoed straight back on the matching
    ``.../state`` topic, so the dashboard and the LLM see the apartment react
    exactly as it would with real hardware attached.
    """

    def __init__(self, settings: Settings, echo: bool = True, echo_delay_s: float = 0.05) -> None:
        self._s = settings
        self._handler: MessageHandler | None = None
        self._echo = echo
        self._echo_delay = echo_delay_s
        self._published = 0
        self._tasks: set[asyncio.Task[None]] = set()
        self.sent: list[tuple[str, bytes]] = []

    @property
    def connected(self) -> bool:
        return True

    async def start(self, handler: MessageHandler) -> None:
        self._handler = handler
        log.info("mqtt disabled -- using loopback transport")

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        self._handler = None

    async def publish(
        self, topic: str, payload: bytes, *, qos: int | None = None, retain: bool = False
    ) -> None:
        self._published += 1
        self.sent.append((topic, payload))
        if not self._echo or self._handler is None or "/set" not in topic:
            return
        echo_topic = topic.replace("/set", "/state", 1)
        task = asyncio.create_task(self._echo_later(echo_topic, payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _echo_later(self, topic: str, payload: bytes) -> None:
        await asyncio.sleep(self._echo_delay)
        handler = self._handler
        if handler is not None:
            with contextlib.suppress(Exception):
                await handler(topic, payload)

    def stats(self) -> dict[str, Any]:
        return {"kind": "loopback", "connected": True, "published": self._published}


def create_transport(settings: Settings) -> MqttTransport:
    return MqttClient(settings) if settings.mqtt_enabled else LoopbackMqtt(settings)
