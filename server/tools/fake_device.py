#!/usr/bin/env python
"""A fake apartment on the MQTT bus.

Subscribes to every device's command topic from ``config/home.yaml``, applies the
change to an in-memory model, and publishes the result on the state topic -- the
same contract the real ESP32 firmware implements. Run it beside the server to
exercise the full MQTT path without any hardware::

    python tools/fake_device.py --host localhost --port 1883

It also publishes retained ``availability`` messages and drifts the sensors
slightly every few seconds, so the dashboard has something alive to show.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiomqtt  # noqa: E402

from app.domain.home import CapabilityKind, load_home_config  # noqa: E402


async def publish_state(client: aiomqtt.Client, device, values: dict) -> None:
    await client.publish(
        device.mqtt.state_topic,
        json.dumps({"state": values}, ensure_ascii=False).encode("utf-8"),
        qos=1,
        retain=True,
    )


async def drift_sensors(client: aiomqtt.Client, home, state: dict, period: float) -> None:
    """Nudge read-only numbers so the UI is not a wall of constants."""
    sensors = [d for d in home.devices if d.is_sensor]
    while True:
        await asyncio.sleep(period)
        for device in sensors:
            values = state[device.id]
            for name, cap in device.capabilities.items():
                if cap.kind is not CapabilityKind.NUMBER:
                    continue
                current = float(values.get(name, cap.default or 0))
                nudged = current + random.uniform(-0.4, 0.4)
                values[name] = round(
                    min(cap.maximum or nudged, max(cap.minimum or nudged, nudged)), 1
                )
            await publish_state(client, device, values)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--base-topic", default="home")
    parser.add_argument("--config", type=Path, default=Path(__file__).parent.parent / "config/home.yaml")
    parser.add_argument("--latency-ms", type=int, default=120, help="simulated actuator delay")
    parser.add_argument("--sensor-period", type=float, default=5.0)
    args = parser.parse_args()

    home = load_home_config(args.config, base_topic=args.base_topic)
    state = {
        d.id: {n: c.default for n, c in d.capabilities.items() if c.default is not None}
        for d in home.devices
    }
    command_index = {d.mqtt.command_topic: d for d in home.devices}

    print(f"căn hộ giả lập: {len(home.devices)} thiết bị -> mqtt://{args.host}:{args.port}")

    async with aiomqtt.Client(
        hostname=args.host,
        port=args.port,
        username=args.username,
        password=args.password,
        identifier="fake-apartment",
    ) as client:
        for device in home.devices:
            await client.publish(device.mqtt.availability_topic, b"online", qos=1, retain=True)
            await publish_state(client, device, state[device.id])
            await client.subscribe(device.mqtt.command_topic, qos=1)
            await client.subscribe(f"{device.mqtt.command_topic}/+", qos=1)

        drifter = asyncio.create_task(
            drift_sensors(client, home, state, args.sensor_period)
        )
        try:
            async for message in client.messages:
                topic = str(message.topic)
                device = command_index.get(topic)
                payload = message.payload or b""

                if device is not None:
                    try:
                        body = json.loads(payload.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    changes = body.get("set", {})
                else:  # the bare-value form: <command_topic>/<capability>
                    parent, _, capability = topic.rpartition("/")
                    device = command_index.get(parent)
                    if device is None:
                        continue
                    changes = {capability: payload.decode("utf-8", "replace")}

                writable = device.writable_capabilities
                applied = {k: v for k, v in changes.items() if k in writable}
                if not applied:
                    continue

                await asyncio.sleep(args.latency_ms / 1000.0)
                state[device.id].update(applied)
                await publish_state(client, device, state[device.id])
                print(f"  {device.id}: {applied}")
        finally:
            drifter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drifter
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
