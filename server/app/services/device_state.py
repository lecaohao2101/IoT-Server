"""Device state manager -- the authoritative, shared view of the apartment.

Reads are on the voice path (the LLM needs to know the lamp is already on), so
state lives in the fast store rather than being polled over MQTT. Writes come from
two directions: *reported* values arriving on MQTT state topics, and *desired*
values we have just published and not yet seen confirmed.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.eventbus import TOPIC_DEVICE_AVAILABILITY, TOPIC_DEVICE_STATE, EventBus
from app.domain.home import Device, HomeConfig
from app.domain.state import DeviceState, DeviceView, HomeSnapshot
from app.storage.backend import KeyValueStore

log = logging.getLogger(__name__)

_KEY = "state:{device_id}"


class DeviceStateManager:
    def __init__(
        self,
        store: KeyValueStore,
        home: HomeConfig,
        bus: EventBus,
        ttl_s: int | None = None,
    ) -> None:
        self._store = store
        self._home = home
        self._bus = bus
        self._ttl = ttl_s or None

    # ------------------------------------------------------------------ read
    async def get(self, device_id: str) -> DeviceState:
        raw = await self._store.get(_KEY.format(device_id=device_id))
        return self._decode(device_id, raw)

    async def snapshot(self) -> HomeSnapshot:
        ids = [d.id for d in self._home.devices]
        raws = await self._store.mget([_KEY.format(device_id=i) for i in ids])
        return HomeSnapshot(
            devices={i: self._decode(i, raw) for i, raw in zip(ids, raws, strict=True)}
        )

    async def views(self, room: str | None = None) -> list[DeviceView]:
        snapshot = await self.snapshot()
        rooms = self._home.room_map
        out: list[DeviceView] = []
        for device in self._home.devices:
            if room and device.room != room:
                continue
            state = snapshot.get(device.id)
            out.append(
                DeviceView(
                    id=device.id,
                    name=device.name,
                    type=device.type.value,
                    room=device.room,
                    room_name=rooms[device.room].name if device.room in rooms else device.room,
                    online=state.online,
                    state=state.attributes,
                    desired=state.desired,
                    capabilities={
                        name: cap.model_dump(exclude_none=True)
                        for name, cap in device.capabilities.items()
                    },
                    updated_at=state.updated_at,
                )
            )
        return out

    # ----------------------------------------------------------------- write
    async def apply_reported(self, device_id: str, values: dict[str, Any]) -> DeviceState:
        """Record what the hardware says it is doing."""
        device = self._home.device_map.get(device_id)
        clean = self._filter_known(device, values) if device else dict(values)
        current = await self.get(device_id)
        updated = current.with_reported(clean)
        await self._persist(updated)
        await self._bus.publish(
            f"{TOPIC_DEVICE_STATE}.{device_id}",
            {"device_id": device_id, "state": updated.attributes, "online": updated.online},
        )
        return updated

    async def apply_desired(self, device_id: str, values: dict[str, Any]) -> DeviceState:
        """Record what we just asked the hardware to do (optimistic UI)."""
        current = await self.get(device_id)
        updated = current.with_desired(values)
        await self._persist(updated)
        await self._bus.publish(
            f"{TOPIC_DEVICE_STATE}.{device_id}",
            {
                "device_id": device_id,
                "state": updated.attributes,
                "desired": updated.desired,
                "online": updated.online,
            },
        )
        return updated

    async def set_availability(self, device_id: str, online: bool) -> DeviceState:
        current = await self.get(device_id)
        if current.online == online and current.last_seen_at is not None:
            return current
        updated = current.with_availability(online)
        await self._persist(updated)
        await self._bus.publish(
            f"{TOPIC_DEVICE_AVAILABILITY}.{device_id}",
            {"device_id": device_id, "online": online},
        )
        return updated

    async def seed_defaults(self) -> int:
        """Give every device a row on first boot so the dashboard is never blank."""
        created = 0
        for device in self._home.devices:
            key = _KEY.format(device_id=device.id)
            if await self._store.get(key) is not None:
                continue
            defaults = {
                name: cap.default
                for name, cap in device.capabilities.items()
                if cap.default is not None
            }
            await self._persist(DeviceState(device_id=device.id, attributes=defaults))
            created += 1
        if created:
            log.info("seeded device state rows", extra={"count": created})
        return created

    async def reset(self, device_id: str) -> None:
        await self._store.delete(_KEY.format(device_id=device_id))

    # ---------------------------------------------------------------- prompt
    def describe(self, snapshot: HomeSnapshot, max_devices: int = 60) -> str:
        """Render current state as compact lines for the LLM context window."""
        rooms = self._home.room_map
        lines: list[str] = []
        for device in self._home.devices[:max_devices]:
            state = snapshot.get(device.id)
            values = state.merged()
            if not values:
                rendered = "chưa có dữ liệu"
            else:
                rendered = ", ".join(f"{k}={_fmt(v)}" for k, v in sorted(values.items()))
            room_name = rooms[device.room].name if device.room in rooms else device.room
            flag = "" if state.online else " [offline]"
            lines.append(f"- {device.id} ({device.name}, {room_name}): {rendered}{flag}")
        return "\n".join(lines) if lines else "(chưa có thiết bị nào báo trạng thái)"

    # --------------------------------------------------------------- helpers
    async def _persist(self, state: DeviceState) -> None:
        await self._store.set(
            _KEY.format(device_id=state.device_id),
            state.model_dump_json(),
            ttl_s=self._ttl,
        )

    @staticmethod
    def _filter_known(device: Device, values: dict[str, Any]) -> dict[str, Any]:
        """Drop attributes the catalogue does not declare, so a chatty firmware
        cannot pollute the context we feed to the model."""
        known = device.capabilities
        clean: dict[str, Any] = {}
        for name, value in values.items():
            cap = known.get(name)
            if cap is None:
                continue
            try:
                coerced, _ = cap.coerce(value)
            except Exception:  # noqa: BLE001 - keep the raw reading, flag nothing
                coerced = value
            clean[name] = coerced
        return clean

    @staticmethod
    def _decode(device_id: str, raw: str | None) -> DeviceState:
        if not raw:
            return DeviceState(device_id=device_id)
        try:
            return DeviceState.model_validate_json(raw)
        except Exception:  # noqa: BLE001 - a corrupt row must not break the pipeline
            log.warning("discarding unreadable device state", extra={"device_id": device_id})
            return DeviceState(device_id=device_id)


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
