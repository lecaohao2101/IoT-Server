"""Offline reasoning stage.

A keyword-and-regex intent matcher over the real device catalogue. It is not a
language model and does not pretend to be one -- its job is to make the entire
pipeline (WebSocket -> STT -> reasoning -> validator -> MQTT -> TTS) demonstrable
and testable with no API key and no network.

It reads the room and the current device state back out of the system prompt the
orchestrator built. That is a small amount of parsing, and it keeps the
:class:`~app.ai.base.LanguageModel` interface identical for every provider.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from app.ai.base import LanguageModel, LlmMessage
from app.core.utils import strip_accents
from app.domain.home import Capability, CapabilityKind, Device, DeviceType, HomeConfig

_ROOM_RE = re.compile(r"Phòng hiện tại:\s*[^(\n]*\(([a-z0-9_]+)\)")
_STATE_LINE_RE = re.compile(r"^- ([a-z0-9_]+) \([^)]*\): (.*)$", re.MULTILINE)
_PERCENT_RE = re.compile(r"(\d{1,3})\s*(?:%|phan tram)")
_DEGREE_RE = re.compile(r"(\d{1,2})\s*(?:do|°)")
_NUMBER_RE = re.compile(r"\b(\d{1,3})\b")

_TYPE_KEYWORDS: tuple[tuple[str, DeviceType], ...] = (
    ("dieu hoa", DeviceType.AIR_CONDITIONER),
    ("may lanh", DeviceType.AIR_CONDITIONER),
    ("binh nong lanh", DeviceType.WATER_HEATER),
    ("nuoc nong", DeviceType.WATER_HEATER),
    ("o cam", DeviceType.OUTLET),
    ("ti vi", DeviceType.TV),
    ("tivi", DeviceType.TV),
    ("tv", DeviceType.TV),
    ("den", DeviceType.LIGHT),
    ("rem", DeviceType.CURTAIN),
    ("quat", DeviceType.FAN),
    ("loa", DeviceType.SPEAKER),
    ("khoa", DeviceType.LOCK),
)

_ON_WORDS = ("bat ", "bat", "mo ", "khoi dong", "len")
_OFF_WORDS = ("tat", "dong ", "ngat", "ngung")
_UP_WORDS = ("tang", "sang hon", "manh hon", "to hon", "cao hon", "them")
_DOWN_WORDS = ("giam", "toi hon", "nho hon", "yeu hon", "thap hon", "bot")
_QUERY_WORDS = ("bao nhieu", "the nao", "kiem tra", "dang bat", "dang tat", "co dang", "hien tai", "nhu the nao")
_ALL_WORDS = ("tat ca", "het", "toan bo", "moi phong", "ca nha")


class MockLanguageModel(LanguageModel):
    name = "mock-llm"

    def __init__(self, home: HomeConfig, chunk_size: int = 24, delay_s: float = 0.0) -> None:
        self._home = home
        self._chunk_size = chunk_size
        self._delay = delay_s

    async def stream(
        self,
        *,
        system: str,
        messages: list[LlmMessage],
        json_schema: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        user_turns = [m.content for m in messages if m.role == "user" and m.content]
        user_text = user_turns[-1] if user_turns else ""
        reply = self._reason(user_text, system, history=list(reversed(user_turns[:-1])))
        payload = json.dumps(reply, ensure_ascii=False)
        for start in range(0, len(payload), self._chunk_size):
            if self._delay:
                await asyncio.sleep(self._delay)
            yield payload[start : start + self._chunk_size]

    # ----------------------------------------------------------- reasoning
    def _reason(self, text: str, system: str, history: list[str] | None = None) -> dict[str, Any]:
        norm = strip_accents(text).lower().strip()
        current_room = self._current_room(system)
        state = self._parse_state(system)

        # 1. Trả lời thân thiện khi người dùng nói lửng lơ hoặc chào hỏi
        _VAGUE_PHRASES = (
            "toi can", "toi muon", "giup toi", "tro ly oi", "alo",
            "chao ban", "ban oi", "co ai khong", "hi", "hello", "giup voi"
        )
        words = [w for w in norm.replace(".", " ").split() if w]
        if (any(norm.startswith(p) for p in _VAGUE_PHRASES) or norm in {"toi can", "toi can."}) and len(words) <= 4 and not self._has_action(norm):
            return {
                "speech": "Mình đây, bạn cần mình hỗ trợ gì ạ? Bật đèn, chỉnh điều hòa hay mở rèm?",
                "commands": [],
                "scene": None,
                "needs_clarification": True,
            }

        # 2. Khớp kịch bản (Scenes)
        scene_id = self._match_scene(norm)
        if scene_id is not None:
            scene = self._home.scene_map[scene_id]
            speech_map = {
                "reading": "Mình đã bật chế độ đọc sách với ánh sáng dịu cho bạn rồi nhé.",
                "good_night": "Chúc bạn ngủ ngon, mình đã chỉnh đèn dịu và đóng rèm rồi nhé.",
                "leaving_home": "Mình đã tắt toàn bộ thiết bị để bạn an tâm ra ngoài nhé.",
                "movie_time": "Mình đã chuyển sang chế độ xem phim rồi nhé.",
                "wake_up": "Chào buổi sáng, mình đã mở rèm đón nắng cho bạn rồi nhé.",
                "relax": "Mình đã bật chế độ thư giãn cho bạn nghỉ ngơi rồi nhé.",
            }
            speech = speech_map.get(scene_id, f"Mình đã kích hoạt {scene.name.lower()} cho bạn rồi nhé.")
            return {
                "speech": speech,
                "commands": [],
                "scene": scene_id,
                "needs_clarification": False,
            }

        # 3. Phản hồi nhu cầu & cảm giác sinh hoạt đời thường (Nóng, Lạnh, Tối, Sáng)
        room = self._match_room(norm) or current_room
        if any(w in norm for w in ("nong qua", "nuc qua", "troi nong", "nong qua di")):
            ac = self._find_device_of_type(DeviceType.AIR_CONDITIONER, room)
            if ac:
                cmds = self._set_ac_comfort(ac, state, temp=25)
                return {
                    "speech": "Trời hơi nóng đúng không? Mình bật điều hòa 25 độ cho mát nhé.",
                    "commands": cmds,
                    "scene": None,
                    "needs_clarification": False,
                }
        if any(w in norm for w in ("lanh qua", "ret qua", "troi lanh")):
            ac = self._find_device_of_type(DeviceType.AIR_CONDITIONER, room)
            if ac:
                cmds = self._set_ac_comfort(ac, state, temp=27)
                return {
                    "speech": "Hơi lạnh đúng không? Mình tăng điều hòa lên 27 độ cho ấm hơn nhé.",
                    "commands": cmds,
                    "scene": None,
                    "needs_clarification": False,
                }
        if any(w in norm for w in ("toi qua", "sao toi the", "phong toi", "toi the")):
            light = self._find_device_of_type(DeviceType.LIGHT, room)
            if light:
                cmds = self._set_light_comfort(light, brightness=100)
                return {
                    "speech": "Phòng hơi tối, để mình bật đèn sáng lên cho bạn nhé.",
                    "commands": cmds,
                    "scene": None,
                    "needs_clarification": False,
                }
        if any(w in norm for w in ("sang qua", "choi qua", "choi mat")):
            light = self._find_device_of_type(DeviceType.LIGHT, room)
            if light:
                cmds = self._set_light_comfort(light, brightness=30)
                return {
                    "speech": "Mình giảm bớt độ sáng đèn cho dịu mắt bạn nhé.",
                    "commands": cmds,
                    "scene": None,
                    "needs_clarification": False,
                }

        targets = self._match_devices(norm, room)
        if not targets:
            # "sáng hơn chút" names nothing: carry the subject over from the
            # previous turns, newest first, the way a real model would.
            targets = self._carry_over(history or [], room)

        if any(word in norm for word in _QUERY_WORDS) and not self._has_action(norm):
            return self._answer_query(targets, state)

        if not targets:
            return {
                "speech": "Bạn muốn điều khiển thiết bị nào ạ? Bật đèn, điều hòa hay rèm cửa?",
                "commands": [],
                "scene": None,
                "needs_clarification": True,
            }

        commands: list[dict[str, Any]] = []
        for device in targets:
            commands.extend(self._commands_for(device, norm, state))

        if not commands:
            device_name = targets[0].name.lower()
            return {
                "speech": f"Bạn muốn mình bật, tắt hay chỉnh gì cho {device_name} ạ?",
                "commands": [],
                "scene": None,
                "needs_clarification": True,
            }

        return {
            "speech": self._describe(targets, commands),
            "commands": commands[:8],
            "scene": None,
            "needs_clarification": False,
        }

    def _find_device_of_type(self, device_type: DeviceType, room: str | None) -> Device | None:
        pool = [d for d in self._home.devices if d.type is device_type]
        if room:
            scoped = [d for d in pool if d.room == room]
            if scoped:
                return scoped[0]
        return pool[0] if pool else None

    @staticmethod
    def _set_ac_comfort(ac: Device, state: dict[str, dict[str, str]], temp: int) -> list[dict[str, Any]]:
        cmds = []
        power_cap = MockLanguageModel._power_capability(ac.writable_capabilities)
        temp_cap = MockLanguageModel._numeric_capability(ac.writable_capabilities, ("temperature", "target_temperature", "setpoint"))
        if power_cap:
            cmds.append(MockLanguageModel._cmd(ac, power_cap, MockLanguageModel._power_value(power_cap, on=True)))
        if temp_cap:
            cmds.append(MockLanguageModel._cmd(ac, temp_cap, str(temp)))
        return cmds

    @staticmethod
    def _set_light_comfort(light: Device, brightness: int) -> list[dict[str, Any]]:
        cmds = []
        power_cap = MockLanguageModel._power_capability(light.writable_capabilities)
        bright_cap = MockLanguageModel._numeric_capability(light.writable_capabilities, ("brightness", "level"))
        if power_cap:
            cmds.append(MockLanguageModel._cmd(light, power_cap, MockLanguageModel._power_value(power_cap, on=True)))
        if bright_cap:
            cmds.append(MockLanguageModel._cmd(light, bright_cap, str(brightness)))
        return cmds

    # ------------------------------------------------------------ matching
    @staticmethod
    def _has_action(norm: str) -> bool:
        words = _ON_WORDS + _OFF_WORDS + _UP_WORDS + _DOWN_WORDS
        return any(w.strip() in norm for w in words)

    def _current_room(self, system: str) -> str | None:
        match = _ROOM_RE.search(system)
        return match.group(1) if match else None

    @staticmethod
    def _parse_state(system: str) -> dict[str, dict[str, str]]:
        state: dict[str, dict[str, str]] = {}
        for device_id, body in _STATE_LINE_RE.findall(system):
            values: dict[str, str] = {}
            for pair in body.split(","):
                key, _, value = pair.partition("=")
                if value:
                    values[key.strip()] = value.strip().removesuffix(" [offline]")
            state[device_id] = values
        return state

    def _match_scene(self, norm: str) -> str | None:
        for scene in self._home.scenes:
            for token in scene.match_tokens():
                if token and len(token) > 3 and token in norm:
                    return scene.id
        return None

    def _match_room(self, norm: str) -> str | None:
        best: tuple[int, str] | None = None
        for room in self._home.rooms:
            for token in room.match_tokens():
                if token and len(token) > 2 and token in norm and (best is None or len(token) > best[0]):
                    best = (len(token), room.id)
        return best[1] if best else None

    def _match_devices(self, norm: str, room: str | None) -> list[Device]:
        named = [d for d in self._home.devices if any(
            token and len(token) > 3 and token in norm for token in d.match_tokens()
        )]
        if named:
            return named

        wanted_type: DeviceType | None = None
        for keyword, device_type in _TYPE_KEYWORDS:
            if keyword in norm:
                wanted_type = device_type
                break
        if wanted_type is None:
            return []

        pool = [d for d in self._home.devices if d.type is wanted_type]
        if any(word in norm for word in _ALL_WORDS):
            return pool
        if room:
            scoped = [d for d in pool if d.room == room]
            if scoped:
                return scoped
        return pool[:1] if len(pool) == 1 else []

    def _carry_over(self, history: list[str], room: str | None) -> list[Device]:
        for previous in history[:4]:
            found = self._match_devices(strip_accents(previous), room)
            if found:
                return found
        return []

    # ------------------------------------------------------------ commands
    def _commands_for(
        self, device: Device, norm: str, state: dict[str, dict[str, str]]
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        caps = device.writable_capabilities
        current = state.get(device.id, {})

        lock = self._lock_command(device, caps, norm)
        if lock is not None:
            return [lock]

        turning_off = any(w in norm for w in _OFF_WORDS)
        turning_on = (not turning_off) and any(w in norm for w in _ON_WORDS)

        power_cap = self._power_capability(caps)
        if power_cap and (turning_on or turning_off):
            out.append(
                self._cmd(device, power_cap, self._power_value(power_cap, on=turning_on))
            )
            if turning_off:
                return out

        percent = _PERCENT_RE.search(norm)
        degree = _DEGREE_RE.search(norm)

        level_cap = self._numeric_capability(caps, ("brightness", "position", "level", "volume", "speed"))
        if percent and level_cap:
            out.append(self._cmd(device, level_cap, percent.group(1)))
        temp_cap = self._numeric_capability(caps, ("temperature", "target_temperature", "setpoint"))
        if degree and temp_cap:
            out.append(self._cmd(device, temp_cap, degree.group(1)))

        if not percent and not degree:
            step_cap = temp_cap if (temp_cap and "do" in norm) else level_cap
            delta = self._relative_delta(norm)
            if step_cap and delta:
                base = self._current_number(current.get(step_cap.name), step_cap)
                target = base + delta * (5 if step_cap is temp_cap else 20)
                out.append(self._cmd(device, step_cap, str(int(target))))
            elif not out:
                bare = _NUMBER_RE.search(norm)
                if bare and level_cap:
                    out.append(self._cmd(device, level_cap, bare.group(1)))

        if out and power_cap and turning_on is False and turning_off is False:
            # Setting a level implies the device should be on.
            already_on = str(current.get(power_cap.name, "")).lower() in {"on", "true", "1"}
            if not already_on:
                out.insert(0, self._cmd(device, power_cap, self._power_value(power_cap, on=True)))
        return out

    def _lock_command(
        self, device: Device, caps: dict[str, Capability], norm: str
    ) -> dict[str, Any] | None:
        """Locks invert the usual vocabulary: "mở khóa" means locked=false.

        Checked before the generic power handling, and unlock before lock, because
        "mo khoa cua" contains the substring "khoa cua".
        """
        cap = caps.get("locked")
        if cap is None or cap.kind is not CapabilityKind.BOOLEAN:
            return None
        if any(w in norm for w in ("mo khoa", "mo cua", "unlock")):
            return self._cmd(device, cap, "false")
        if any(w in norm for w in ("khoa cua", "khoa lai", "dong cua", "lock")):
            return self._cmd(device, cap, "true")
        return None

    @staticmethod
    def _cmd(device: Device, cap: Capability, value: str) -> dict[str, Any]:
        return {
            "device_id": device.id,
            "capability": cap.name,
            "value": str(value),
            "delay_s": 0.0,
        }

    @staticmethod
    def _power_capability(caps: dict[str, Capability]) -> Capability | None:
        for name in ("power", "state", "on", "open"):
            if name in caps:
                return caps[name]
        return None

    @staticmethod
    def _power_value(cap: Capability, *, on: bool) -> str:
        if cap.kind is CapabilityKind.BOOLEAN:
            return "true" if on else "false"
        if cap.kind is CapabilityKind.ENUM:
            wanted = ("on", "open", "true") if on else ("off", "closed", "close", "false")
            for value in cap.values:
                if strip_accents(value) in wanted:
                    return value
            return cap.values[0] if on else cap.values[-1]
        return "on" if on else "off"

    @staticmethod
    def _numeric_capability(caps: dict[str, Capability], names: tuple[str, ...]) -> Capability | None:
        for name in names:
            cap = caps.get(name)
            if cap is not None and cap.kind is CapabilityKind.NUMBER:
                return cap
        return None

    @staticmethod
    def _relative_delta(norm: str) -> int:
        if any(w in norm for w in _UP_WORDS):
            return 1
        if any(w in norm for w in _DOWN_WORDS):
            return -1
        return 0

    @staticmethod
    def _current_number(raw: str | None, cap: Capability) -> float:
        try:
            return float(raw) if raw is not None else float(cap.minimum or 0)
        except (TypeError, ValueError):
            return float(cap.minimum or 0)

    # --------------------------------------------------------------- speech
    def _answer_query(
        self, targets: list[Device], state: dict[str, dict[str, str]]
    ) -> dict[str, Any]:
        if not targets:
            return {
                "speech": "Bạn muốn hỏi về thiết bị nào ạ?",
                "commands": [],
                "scene": None,
                "needs_clarification": True,
            }
        parts: list[str] = []
        for device in targets[:3]:
            values = state.get(device.id, {})
            if not values:
                parts.append(f"{device.name} chưa có dữ liệu")
                continue
            rendered = ", ".join(f"{k} {v}" for k, v in list(values.items())[:3])
            parts.append(f"{device.name}: {rendered}")
        return {
            "speech": ". ".join(parts) + ".",
            "commands": [],
            "scene": None,
            "needs_clarification": False,
        }

    @staticmethod
    def _describe(targets: list[Device], commands: list[dict[str, Any]]) -> str:
        names = ", ".join(dict.fromkeys(d.name for d in targets))
        lock = next((c for c in commands if c["capability"] == "locked"), None)
        if lock is not None:
            return f"Đã khóa {names}." if lock["value"] == "true" else f"Mình sẽ mở {names}."
        powered_off = any(
            c["capability"] in {"power", "state", "on", "open"}
            and str(c["value"]).lower() in {"off", "false", "closed", "đóng", "tắt"}
            for c in commands
        )
        if powered_off:
            return f"Đã tắt {names}."
        levels = [c for c in commands if c["capability"] not in {"power", "state", "on", "open"}]
        if levels:
            detail = ", ".join(f"{c['capability']} {c['value']}" for c in levels[:2])
            return f"Đã chỉnh {names}: {detail}."
        return f"Đã bật {names}."
