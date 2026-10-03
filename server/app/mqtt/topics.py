"""MQTT topic conventions and payload codecs.

Layout (``MQTT_BASE_TOPIC`` defaults to ``home``)::

    home/<room>/<device_id>/set            server -> device, command
    home/<room>/<device_id>/set/<cap>      ... bare-value variant for dumb firmware
    home/<room>/<device_id>/state          device -> server, reported attributes
    home/<room>/<device_id>/state/<cap>    ... bare-value variant
    home/<room>/<device_id>/availability   device -> server, retained online/offline
    home/server/status                     server LWT, retained

Topics are not derived here at runtime: each device's binding is resolved once in
``HomeConfig.apply_topic_defaults`` so an existing installation can keep whatever
topic scheme its firmware already uses.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.utils import new_id, utc_iso

ONLINE = "online"
OFFLINE = "offline"


def server_status_topic(base: str) -> str:
    return f"{base}/server/status"


def subscription_filters(base: str) -> list[str]:
    """Wildcards covering every inbound topic the server cares about."""
    return [f"{base}/+/+/state", f"{base}/+/+/state/+", f"{base}/+/+/availability"]


def encode_command(
    *,
    device_id: str,
    values: dict[str, Any],
    plan_id: str,
    delay_s: float = 0.0,
) -> bytes:
    payload = {
        "id": new_id("cmd"),
        "plan_id": plan_id,
        "ts": utc_iso(),
        "device_id": device_id,
        "set": values,
    }
    if delay_s > 0:
        payload["delay_s"] = round(delay_s, 3)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def encode_value(value: Any) -> bytes:
    """Bare-value payload for the ``.../set/<capability>`` form."""
    if isinstance(value, bool):
        return b"true" if value else b"false"
    if isinstance(value, (int, float, str)):
        return str(value).encode("utf-8")
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def decode_state_payload(payload: bytes) -> dict[str, Any] | None:
    """Parse a device state message. Returns None when it is not a usable mapping."""
    text = payload.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        # Firmware often wraps the attributes: {"state": {...}} or {"reported": {...}}.
        # "set" appears when the loopback transport echoes our own command back.
        for wrapper in ("state", "reported", "attributes", "set"):
            inner = parsed.get(wrapper)
            if isinstance(inner, dict):
                return inner
        return {
            k: v
            for k, v in parsed.items()
            if k not in {"ts", "id", "device_id", "plan_id", "delay_s"}
        }
    return None


def decode_scalar(payload: bytes) -> Any:
    """Parse a bare-value payload, keeping JSON types where the device sent them."""
    text = payload.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"true", "on"}:
        return True
    if lowered in {"false", "off"}:
        return False
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def decode_availability(payload: bytes) -> bool | None:
    text = payload.decode("utf-8", errors="replace").strip().lower()
    if text in {ONLINE, "true", "1", "available"}:
        return True
    if text in {OFFLINE, "false", "0", "unavailable"}:
        return False
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict) and "online" in parsed:
        return bool(parsed["online"])
    return None
