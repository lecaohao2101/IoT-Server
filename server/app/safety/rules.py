"""Declarative safety policy loaded from ``config/safety.yaml``.

These rules are deliberately *not* prompt instructions. A language model can be
talked out of a prompt; it cannot be talked out of a rule that runs after it.
Everything the hardware is allowed to receive passes through this policy.
"""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import ConfigError
from app.domain.home import Device


def _parse_hhmm(value: Any) -> time:
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, int):
        # PyYAML reads an unquoted `22:00` as the sexagesimal integer 1320.
        return time(hour=(value // 60) % 24, minute=value % 60)
    text = str(value).strip()
    parts = text.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        raise ValueError(f"'{value}' is not a HH:MM time") from None
    return time(hour=hour, minute=minute)


class Selector(BaseModel):
    """Matches a command by device id, device type, room and/or capability.

    An unset field matches everything, so ``{capability: volume}`` applies to every
    device that has a volume.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    device_id: str | None = None
    device_type: str | None = None
    room: str | None = None
    capability: str | None = None

    def matches(self, device: Device, capability: str) -> bool:
        if self.device_id and self.device_id != device.id:
            return False
        if self.device_type and self.device_type != device.type.value:
            return False
        if self.room and self.room != device.room:
            return False
        return not (self.capability and self.capability != capability)

    @model_validator(mode="after")
    def _not_empty(self) -> Selector:
        if not any((self.device_id, self.device_type, self.room, self.capability)):
            raise ValueError("selector must constrain at least one of device_id/device_type/room/capability")
        return self


class LimitRule(BaseModel):
    """Narrows a numeric capability beyond what the device itself allows."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    match: Selector
    minimum: float | None = None
    maximum: float | None = None
    quiet_hours_only: bool = False
    message: str | None = None

    @model_validator(mode="after")
    def _has_bound(self) -> LimitRule:
        if self.minimum is None and self.maximum is None:
            raise ValueError("limit rule needs a minimum or a maximum")
        return self


class ForbidRule(BaseModel):
    """Blocks a command outright, optionally only for a specific value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    match: Selector
    value: Any = None
    quiet_hours_only: bool = False
    message: str = "Hành động này không được phép."


class ConfirmRule(BaseModel):
    """Forces a human yes/no before the command reaches the hardware."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    match: Selector
    value: Any = None
    prompt: str = "Bạn xác nhận thực hiện thao tác này chứ?"


class QuietHours(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    start: time = Field(default=time(22, 0))
    end: time = Field(default=time(6, 0))
    note: str = "đang trong khung giờ yên tĩnh"

    @field_validator("start", "end", mode="before")
    @classmethod
    def _coerce_time(cls, value: Any) -> time:
        return _parse_hhmm(value)

    def contains(self, moment: datetime) -> bool:
        if not self.enabled:
            return False
        now = moment.time()
        if self.start <= self.end:
            return self.start <= now < self.end
        return now >= self.start or now < self.end  # window wraps midnight


class RateLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    commands_per_device_per_minute: float = 20.0
    device_burst: float = 6.0
    plans_per_session_per_minute: float = 30.0
    session_burst: float = 10.0


class SafetyPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_commands_per_plan: int = Field(default=8, ge=1, le=64)
    max_delay_s: float = Field(default=3600.0, ge=0.0)
    require_device_online: bool = False
    quiet_hours: QuietHours = Field(default_factory=QuietHours)
    limits: tuple[LimitRule, ...] = ()
    forbid: tuple[ForbidRule, ...] = ()
    confirm: tuple[ConfirmRule, ...] = ()
    rate_limits: RateLimits = Field(default_factory=RateLimits)

    def limits_for(self, device: Device, capability: str, quiet: bool) -> list[LimitRule]:
        return [
            rule
            for rule in self.limits
            if rule.match.matches(device, capability) and (quiet or not rule.quiet_hours_only)
        ]

    def forbid_for(self, device: Device, capability: str, quiet: bool) -> list[ForbidRule]:
        return [
            rule
            for rule in self.forbid
            if rule.match.matches(device, capability) and (quiet or not rule.quiet_hours_only)
        ]

    def confirm_for(self, device: Device, capability: str) -> list[ConfirmRule]:
        return [rule for rule in self.confirm if rule.match.matches(device, capability)]


DEFAULT_POLICY = SafetyPolicy()


def load_safety_policy(path: Path) -> SafetyPolicy:
    """Read and validate the safety policy; a missing file yields the strict default."""
    if not path.exists():
        return DEFAULT_POLICY
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Safety policy is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("Safety policy must be a YAML mapping")
    try:
        return SafetyPolicy.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"Invalid safety policy: {exc}") from exc
