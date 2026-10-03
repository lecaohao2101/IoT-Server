"""Runtime state of the apartment: what each device reports right now."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.utils import utc_iso


class DeviceState(BaseModel):
    """Last known values for one device, plus liveness bookkeeping.

    ``attributes`` holds capability -> value. ``desired`` holds values we have
    published but not yet seen echoed back, so the dashboard can show an optimistic
    state without lying about what the hardware confirmed.
    """

    model_config = ConfigDict(extra="forbid")

    device_id: str
    online: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)
    desired: dict[str, Any] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=utc_iso)
    last_seen_at: str | None = None

    def merged(self) -> dict[str, Any]:
        """Reported values, overlaid with any still-unconfirmed desired values."""
        return {**self.attributes, **self.desired}

    def with_reported(self, values: dict[str, Any]) -> DeviceState:
        attributes = {**self.attributes, **values}
        desired = {k: v for k, v in self.desired.items() if attributes.get(k) != v}
        now = utc_iso()
        return self.model_copy(
            update={
                "attributes": attributes,
                "desired": desired,
                "updated_at": now,
                "last_seen_at": now,
                "online": True,
            }
        )

    def with_desired(self, values: dict[str, Any]) -> DeviceState:
        return self.model_copy(
            update={"desired": {**self.desired, **values}, "updated_at": utc_iso()}
        )

    def with_availability(self, online: bool) -> DeviceState:
        now = utc_iso()
        return self.model_copy(
            update={
                "online": online,
                "updated_at": now,
                "last_seen_at": now if online else self.last_seen_at,
            }
        )


class DeviceView(BaseModel):
    """Device descriptor joined with its state -- the dashboard's row model."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: str
    room: str
    room_name: str
    online: bool
    state: dict[str, Any]
    desired: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    updated_at: str | None = None


class HomeSnapshot(BaseModel):
    """Everything the LLM and the dashboard need to know about right now."""

    model_config = ConfigDict(extra="forbid")

    taken_at: str = Field(default_factory=utc_iso)
    devices: dict[str, DeviceState] = Field(default_factory=dict)

    def get(self, device_id: str) -> DeviceState:
        return self.devices.get(device_id) or DeviceState(device_id=device_id)

    def value(self, device_id: str, capability: str, default: Any = None) -> Any:
        return self.get(device_id).merged().get(capability, default)
