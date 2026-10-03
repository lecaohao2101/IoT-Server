"""The apartment described as data.

``config/home.yaml`` is the single source of truth for what exists and what may be
done to it. The LLM prompt, the safety validator, the MQTT topic map and the REST
dashboard are all projections of this one document -- so adding a device is a
config change, never a code change.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import ConfigError, NotFoundError, ValidationError
from app.core.utils import fold, strip_accents


class CapabilityKind(str, Enum):
    ENUM = "enum"
    NUMBER = "number"
    BOOLEAN = "boolean"
    STRING = "string"


class DeviceType(str, Enum):
    LIGHT = "light"
    AIR_CONDITIONER = "air_conditioner"
    CURTAIN = "curtain"
    FAN = "fan"
    OUTLET = "outlet"
    TV = "tv"
    SPEAKER = "speaker"
    LOCK = "lock"
    SENSOR = "sensor"
    WATER_HEATER = "water_heater"
    OTHER = "other"


class Capability(BaseModel):
    """One controllable (or observable) attribute of a device."""

    model_config = ConfigDict(frozen=True)

    name: str
    kind: CapabilityKind
    description: str | None = None
    unit: str | None = None

    values: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None

    read_only: bool = False
    sensitive: bool = False  # needs explicit user confirmation before dispatch
    default: Any = None

    @model_validator(mode="after")
    def _check_shape(self) -> Capability:
        if self.kind is CapabilityKind.ENUM and not self.values:
            raise ValueError(f"capability '{self.name}': enum needs a non-empty 'values' list")
        if self.kind is CapabilityKind.NUMBER and (self.minimum is None or self.maximum is None):
            raise ValueError(f"capability '{self.name}': number needs 'minimum' and 'maximum'")
        if (
            self.kind is CapabilityKind.NUMBER
            and self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError(f"capability '{self.name}': minimum is above maximum")
        return self

    # ------------------------------------------------------------------ coerce
    def coerce(self, value: Any) -> tuple[Any, str | None]:
        """Normalise a model-supplied value to this capability's domain.

        Returns ``(value, note)`` where ``note`` describes a clamp that was applied.
        Raises :class:`ValidationError` when the value cannot be rescued.
        """
        if self.kind is CapabilityKind.BOOLEAN:
            return self._coerce_bool(value), None
        if self.kind is CapabilityKind.NUMBER:
            return self._coerce_number(value)
        if self.kind is CapabilityKind.ENUM:
            return self._coerce_enum(value), None
        return str(value), None

    def _coerce_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        token = fold(str(value))
        if token in {"true", "on", "1", "yes", "bat", "mo", "bật", "mở"}:
            return True
        if token in {"false", "off", "0", "no", "tat", "dong", "tắt", "đóng"}:
            return False
        raise ValidationError(
            f"'{value}' is not a boolean for capability '{self.name}'",
            details={"capability": self.name},
        )

    def _coerce_number(self, value: Any) -> tuple[float | int, str | None]:
        try:
            number = float(str(value).replace(",", ".").strip().rstrip("%°C"))
        except (TypeError, ValueError):
            raise ValidationError(
                f"'{value}' is not a number for capability '{self.name}'",
                details={"capability": self.name},
            ) from None

        note: str | None = None
        low, high = self.minimum, self.maximum
        if low is not None and number < low:
            note = f"{self.name} raised from {_pretty(number)} to the minimum {_pretty(low)}"
            number = low
        elif high is not None and number > high:
            note = f"{self.name} lowered from {_pretty(number)} to the maximum {_pretty(high)}"
            number = high

        if self.step:
            base = low if low is not None else 0.0
            number = base + round((number - base) / self.step) * self.step

        rounded = round(number, 3)
        as_int = int(rounded)
        return (as_int if rounded == as_int else rounded), note

    def _coerce_enum(self, value: Any) -> str:
        token = fold(str(value))
        for allowed in self.values:
            if fold(allowed) == token or strip_accents(allowed) == strip_accents(token):
                return allowed
        raise ValidationError(
            f"'{value}' is not one of {list(self.values)} for capability '{self.name}'",
            details={"capability": self.name, "allowed": list(self.values)},
        )


def _pretty(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else f"{number:g}"


class MqttBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    command_topic: str | None = None
    state_topic: str | None = None
    availability_topic: str | None = None
    #: ``json`` publishes ``{"capability": value, ...}``; ``value`` publishes the bare
    #: value on a per-capability subtopic, for dumb firmware.
    payload_style: str = Field(default="json", pattern="^(json|value)$")


class Room(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    aliases: tuple[str, ...] = ()

    def match_tokens(self) -> set[str]:
        return _tokens([self.id, self.name, *self.aliases])


class Device(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    type: DeviceType = DeviceType.OTHER
    room: str
    aliases: tuple[str, ...] = ()
    capabilities: dict[str, Capability]
    mqtt: MqttBinding = Field(default_factory=MqttBinding)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _inflate(cls, value: Any) -> Any:
        """Allow the compact YAML form ``brightness: {kind: number, ...}``."""
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for name, spec in value.items():
                if isinstance(spec, dict) and "name" not in spec:
                    spec = {**spec, "name": name}
                out[name] = spec
            return out
        return value

    @property
    def is_sensor(self) -> bool:
        return self.type is DeviceType.SENSOR or all(c.read_only for c in self.capabilities.values())

    @property
    def writable_capabilities(self) -> dict[str, Capability]:
        return {k: v for k, v in self.capabilities.items() if not v.read_only}

    def capability(self, name: str) -> Capability:
        cap = self.capabilities.get(name)
        if cap is None:
            raise NotFoundError(
                f"Device '{self.id}' has no capability '{name}'",
                details={"device_id": self.id, "available": sorted(self.capabilities)},
            )
        return cap

    def match_tokens(self) -> set[str]:
        return _tokens([self.id, self.name, *self.aliases])


class Scene(BaseModel):
    """A named bundle of commands, e.g. "ngủ ngon" -> dim lights, close curtains."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    aliases: tuple[str, ...] = ()
    description: str | None = None
    actions: tuple[dict[str, Any], ...] = ()

    def match_tokens(self) -> set[str]:
        return _tokens([self.id, self.name, *self.aliases])


def _tokens(values: list[str]) -> set[str]:
    out: set[str] = set()
    for value in values:
        if value:
            out.add(fold(value))
            out.add(strip_accents(value))
    return out


class HomeConfig(BaseModel):
    """Validated apartment description with lookup indices built once at startup."""

    model_config = ConfigDict(frozen=True)

    name: str = "Smart Apartment"
    timezone: str = "Asia/Ho_Chi_Minh"
    language: str = "vi-VN"
    rooms: tuple[Room, ...] = ()
    devices: tuple[Device, ...] = ()
    scenes: tuple[Scene, ...] = ()

    @model_validator(mode="after")
    def _check_integrity(self) -> HomeConfig:
        room_ids = {r.id for r in self.rooms}
        if len(room_ids) != len(self.rooms):
            raise ValueError("duplicate room id in home config")

        device_ids = {d.id for d in self.devices}
        if len(device_ids) != len(self.devices):
            raise ValueError("duplicate device id in home config")

        for device in self.devices:
            if device.room not in room_ids:
                raise ValueError(f"device '{device.id}' references unknown room '{device.room}'")
            if not device.capabilities:
                raise ValueError(f"device '{device.id}' declares no capabilities")

        for scene in self.scenes:
            for action in scene.actions:
                target = action.get("device_id")
                if target not in device_ids:
                    raise ValueError(f"scene '{scene.id}' references unknown device '{target}'")
        return self

    # ----------------------------------------------------------------- lookups
    @property
    def device_map(self) -> dict[str, Device]:
        return {d.id: d for d in self.devices}

    @property
    def room_map(self) -> dict[str, Room]:
        return {r.id: r for r in self.rooms}

    @property
    def scene_map(self) -> dict[str, Scene]:
        return {s.id: s for s in self.scenes}

    def device(self, device_id: str) -> Device:
        found = self.device_map.get(device_id)
        if found is None:
            raise NotFoundError(
                f"Unknown device '{device_id}'",
                details={"device_id": device_id, "known": sorted(self.device_map)},
            )
        return found

    def has_device(self, device_id: str) -> bool:
        return device_id in self.device_map

    def devices_in_room(self, room_id: str) -> list[Device]:
        return [d for d in self.devices if d.room == room_id]

    def resolve_room(self, text: str) -> Room | None:
        wanted = {fold(text), strip_accents(text)}
        for room in self.rooms:
            if room.match_tokens() & wanted:
                return room
        return None

    def resolve_devices(self, text: str) -> list[Device]:
        """Best-effort alias lookup, used to repair a model that invented an id."""
        wanted = {fold(text), strip_accents(text)}
        exact = [d for d in self.devices if d.match_tokens() & wanted]
        if exact:
            return exact
        needle = strip_accents(text)
        if len(needle) < 3:
            return []
        return [d for d in self.devices if any(needle in token for token in d.match_tokens())]

    def resolve_scene(self, text: str) -> Scene | None:
        wanted = {fold(text), strip_accents(text)}
        for scene in self.scenes:
            if scene.match_tokens() & wanted:
                return scene
        return None

    # -------------------------------------------------------- MQTT topic map
    def apply_topic_defaults(self, base_topic: str) -> HomeConfig:
        """Fill in conventional MQTT topics for devices that did not declare any."""
        patched: list[Device] = []
        for device in self.devices:
            binding = device.mqtt
            patched.append(
                device.model_copy(
                    update={
                        "mqtt": MqttBinding(
                            command_topic=binding.command_topic
                            or f"{base_topic}/{device.room}/{device.id}/set",
                            state_topic=binding.state_topic
                            or f"{base_topic}/{device.room}/{device.id}/state",
                            availability_topic=binding.availability_topic
                            or f"{base_topic}/{device.room}/{device.id}/availability",
                            payload_style=binding.payload_style,
                        )
                    }
                )
            )
        return self.model_copy(update={"devices": tuple(patched)})

    def state_topic_index(self) -> dict[str, Device]:
        return {d.mqtt.state_topic: d for d in self.devices if d.mqtt.state_topic}

    def availability_topic_index(self) -> dict[str, Device]:
        return {d.mqtt.availability_topic: d for d in self.devices if d.mqtt.availability_topic}


def load_home_config(path: Path, base_topic: str = "home") -> HomeConfig:
    """Read and validate ``home.yaml``; raises :class:`ConfigError` on any problem."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"Home config not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Home config is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Home config must be a YAML mapping")

    try:
        config = HomeConfig.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError and friends
        raise ConfigError(f"Invalid home config: {exc}") from exc

    return config.apply_topic_defaults(base_topic)
