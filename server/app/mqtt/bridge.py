"""The seam between the apartment's hardware and the server's model of it.

Downstream: takes a validated :class:`CommandPlan` and publishes it, grouped into
one message per device so a lamp receives ``{"power":"on","brightness":70}`` as a
single atomic change rather than two racing messages.

Upstream: turns ``.../state`` and ``.../availability`` messages back into state
manager updates, which in turn fan out to every connected dashboard.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from typing import Any

from app.core.errors import TransportError
from app.core.eventbus import TOPIC_COMMAND_DISPATCHED, EventBus
from app.domain.commands import CommandPlan, DispatchReport, DispatchResult, ValidatedCommand
from app.domain.home import Device, HomeConfig
from app.mqtt.client import MqttTransport
from app.mqtt.topics import (
    decode_availability,
    decode_scalar,
    decode_state_payload,
    encode_command,
    encode_value,
)
from app.services.device_state import DeviceStateManager

log = logging.getLogger(__name__)


class DeviceBridge:
    def __init__(
        self,
        transport: MqttTransport,
        home: HomeConfig,
        state: DeviceStateManager,
        bus: EventBus,
        qos: int = 1,
    ) -> None:
        self._transport = transport
        self._home = home
        self._state = state
        self._bus = bus
        self._qos = qos
        self._state_index = home.state_topic_index()
        self._availability_index = home.availability_topic_index()
        self._delayed: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        await self._transport.start(self.handle_message)

    async def stop(self) -> None:
        for task in list(self._delayed):
            task.cancel()
        for task in list(self._delayed):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._delayed.clear()
        await self._transport.stop()

    # ------------------------------------------------------------- inbound
    async def handle_message(self, topic: str, payload: bytes) -> None:
        device = self._state_index.get(topic)
        if device is not None:
            values = decode_state_payload(payload)
            if values:
                await self._state.apply_reported(device.id, values)
            return

        device = self._availability_index.get(topic)
        if device is not None:
            online = decode_availability(payload)
            if online is not None:
                await self._state.set_availability(device.id, online)
            return

        # ``.../state/<capability>`` -- one attribute per message.
        parent, _, leaf = topic.rpartition("/")
        device = self._state_index.get(parent)
        if device is not None and leaf:
            await self._state.apply_reported(device.id, {leaf: decode_scalar(payload)})
            return

        log.debug("ignoring unmapped mqtt topic", extra={"topic": topic})

    # ------------------------------------------------------------ outbound
    async def dispatch(self, plan: CommandPlan) -> DispatchReport:
        """Publish every accepted command. Delayed commands are scheduled, not slept on."""
        immediate: list[ValidatedCommand] = []
        deferred: list[ValidatedCommand] = []
        for command in plan.accepted:
            (deferred if command.delay_s > 0 else immediate).append(command)

        results = await self._publish_group(plan.id, immediate)

        for command in deferred:
            task = asyncio.create_task(self._publish_later(plan.id, command))
            self._delayed.add(task)
            task.add_done_callback(self._delayed.discard)
            results.append(
                DispatchResult(
                    command=command,
                    delivered=True,
                    topic=self._command_topic(command),
                    error=None,
                )
            )

        report = DispatchReport(plan_id=plan.id, results=tuple(results))
        if report.results:
            await self._bus.publish(
                TOPIC_COMMAND_DISPATCHED,
                {
                    "plan_id": plan.id,
                    "delivered": report.delivered_count,
                    "failed": len(report.failed),
                    "commands": [r.command.model_dump() for r in report.results if r.delivered],
                },
            )
        return report

    async def _publish_later(self, plan_id: str, command: ValidatedCommand) -> None:
        await asyncio.sleep(command.delay_s)
        try:
            await self._publish_group(plan_id, [command])
        except Exception:  # noqa: BLE001 - a scheduled failure has no caller to raise to
            log.exception(
                "delayed command failed",
                extra={"plan_id": plan_id, "device_id": command.device_id},
            )

    async def _publish_group(
        self, plan_id: str, commands: list[ValidatedCommand]
    ) -> list[DispatchResult]:
        by_device: dict[str, list[ValidatedCommand]] = defaultdict(list)
        for command in commands:
            by_device[command.device_id].append(command)

        results: list[DispatchResult] = []
        for device_id, group in by_device.items():
            device = self._home.device_map.get(device_id)
            if device is None or not device.mqtt.command_topic:
                error = f"device '{device_id}' has no command topic"
                results.extend(
                    DispatchResult(command=c, delivered=False, error=error) for c in group
                )
                continue

            values = {c.capability: c.value for c in group}
            delay = max((c.delay_s for c in group), default=0.0)
            try:
                topics = await self._send(device, values, plan_id=plan_id, delay_s=delay)
            except TransportError as exc:
                log.warning(
                    "command delivery failed",
                    extra={"device_id": device_id, "error": exc.message},
                )
                results.extend(
                    DispatchResult(command=c, delivered=False, error=exc.message) for c in group
                )
                continue

            await self._state.apply_desired(device_id, values)
            results.extend(
                DispatchResult(command=c, delivered=True, topic=topics[0]) for c in group
            )
        return results

    async def _send(
        self, device: Device, values: dict[str, Any], *, plan_id: str, delay_s: float
    ) -> list[str]:
        base = device.mqtt.command_topic or ""
        if device.mqtt.payload_style == "value":
            topics = []
            for capability, value in values.items():
                topic = f"{base}/{capability}"
                await self._transport.publish(topic, encode_value(value), qos=self._qos)
                topics.append(topic)
            return topics

        payload = encode_command(
            device_id=device.id, values=values, plan_id=plan_id, delay_s=delay_s
        )
        await self._transport.publish(base, payload, qos=self._qos)
        return [base]

    def _command_topic(self, command: ValidatedCommand) -> str | None:
        device = self._home.device_map.get(command.device_id)
        return device.mqtt.command_topic if device else None
