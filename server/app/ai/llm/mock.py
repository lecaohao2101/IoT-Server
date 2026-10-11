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
        reply = self._reason(
            user_text,
            system,
            history=list(reversed(user_turns[:-1])),
            messages=messages,
        )
        payload = json.dumps(reply, ensure_ascii=False)
        for start in range(0, len(payload), self._chunk_size):
            if self._delay:
                await asyncio.sleep(self._delay)
            yield payload[start : start + self._chunk_size]

    # ----------------------------------------------------------- reasoning
    def _reason(
        self,
        text: str,
        system: str,
        history: list[str] | None = None,
        messages: list[LlmMessage] | None = None,
    ) -> dict[str, Any]:
        norm = strip_accents(text).lower().strip()
        current_room = self._current_room(system)
        state = self._parse_state(system)
        room = self._match_room(norm) or current_room

        # Lấy lịch sử hội thoại gần nhất (assistant và user)
        last_assistant = ""
        if messages:
            assistants = [m.content for m in messages if m.role == "assistant" and m.content]
            if assistants:
                last_assistant = assistants[-1]
        last_assistant_norm = strip_accents(last_assistant).lower().strip()
        prev_user_norm = strip_accents(history[0]).lower().strip() if (history and history[0]) else ""

        # -------------------------------------------------------
        # 1. GIAO TIẾP TỰ NHIÊN GIỮA NGƯỜI VỚI NGƯỜI (Small-talk & Empathy)
        # -------------------------------------------------------
        # 1.1 Lời cảm ơn
        if any(w in norm for w in ("cam on", "thank you", "thanks", "cam on nha", "cam on nhe")):
            gratitude_replies = [
                "Dạ không có chi đâu nè! Cần gì bạn cứ gọi mình nhé.",
                "Rất vui được hỗ trợ bạn nè! Chúc bạn một ngày thật vui vẻ nhé.",
                "Dạ có gì đâu bạn ơi, giúp bạn là niềm vui của mình mà.",
            ]
            idx = sum(ord(c) for c in norm) % len(gratitude_replies)
            return {
                "speech": gratitude_replies[idx],
                "commands": [],
                "scene": None,
                "needs_clarification": False,
            }

        # 1.2 Mệt mỏi / Đi làm về
        if any(w in norm for w in ("met qua", "met moi", "met qua di", "di lam met", "duoi qua", "met moi qua")):
            return {
                "speech": "Thương bạn ghê, bạn đã vất vả cả ngày rồi! Bạn nghỉ ngơi chút nha, có cần mình bật điều hòa mát hay mở nhạc thư giãn cho bạn không nè?",
                "commands": [],
                "scene": None,
                "needs_clarification": True,
            }

        # 1.3 Chúc ngủ ngon
        if any(w in norm for w in ("chuc ngu ngon", "ngu ngon nhe", "ngu ngon nha")):
            return {
                "speech": "Chúc bạn có một giấc ngủ thật ngon và những giấc mơ đẹp nhé!",
                "commands": [],
                "scene": None,
                "needs_clarification": False,
            }

        # 1.4 Danh tính & Thăm hỏi
        if any(w in norm for w in ("ban la ai", "ban ten gi", "gioi thieu ban than")):
            return {
                "speech": "Mình là người bạn trợ lý thông minh của căn hộ, luôn ở đây để đồng hành và hỗ trợ bạn điều khiển ngôi nhà thân yêu nè!",
                "commands": [],
                "scene": None,
                "needs_clarification": False,
            }
        if any(w in norm for w in ("ban co khoe khong", "khoe khong")):
            return {
                "speech": "Mình luôn tràn đầy năng lượng và sẵn sàng giúp bạn nè! Bạn hôm nay thế nào rồi?",
                "commands": [],
                "scene": None,
                "needs_clarification": False,
            }

        # 1.5 Chào hỏi xã giao
        _GREETINGS = ("chao ban", "xin chao", "chao tro ly", "hi ban", "hello", "alo tro ly")
        if any(norm == w or norm.startswith(w + " ") for w in _GREETINGS) and not self._has_action(norm) and len(norm.split()) <= 4:
            return {
                "speech": "Chào bạn! Rất vui được trò chuyện với bạn. Hôm nay bạn cần mình hỗ trợ gì không nè?",
                "commands": [],
                "scene": None,
                "needs_clarification": True,
            }

        # 1.6 Câu lửng lơ
        _VAGUE_PHRASES = (
            "toi can", "toi muon", "giup toi", "tro ly oi", "alo",
            "ban oi", "co ai khong", "giup voi"
        )
        words = [w for w in norm.replace(".", " ").split() if w]
        if (any(norm.startswith(p) for p in _VAGUE_PHRASES) or norm in {"toi can", "toi can."}) and len(words) <= 4 and not self._has_action(norm):
            return {
                "speech": "Mình đây, bạn cần mình hỗ trợ gì nè? Bật đèn, chỉnh điều hòa hay mở rèm?",
                "commands": [],
                "scene": None,
                "needs_clarification": True,
            }

        # -------------------------------------------------------
        # 2. TIẾP NỐI NGỮ CẢNH TỪ LƯỢT TRƯỚC (Multi-Turn Continuity)
        # -------------------------------------------------------
        # 2.1 Người dùng xác nhận đề xuất trước đó (vd: "buồn ngủ quá" -> "có cần tắt đèn..." -> "ừ/tắt đi")
        _AFFIRMATIVES = ("co", "u", "uh", "ok", "duoc", "vang", "dong y", "nho ban", "giup minh", "lam di")
        is_affirmative = norm in _AFFIRMATIVES or any(norm.startswith(w + " ") for w in _AFFIRMATIVES)
        is_turn_off_confirm = is_affirmative or any(norm == w or norm.startswith(w + " ") for w in ("tat di", "tat den di", "tat giup"))
        is_turn_on_confirm = is_affirmative or any(norm == w or norm.startswith(w + " ") for w in ("bat di", "bat len", "bat giup", "bat den di"))

        if last_assistant_norm:
            # Tìm xem lượt trước trợ lý đang nói về thiết bị cụ thể nào
            referred_device = None
            for d in self._home.devices:
                if d.name.lower() in last_assistant_norm or any(token and len(token) > 3 and token in last_assistant_norm for token in d.match_tokens()):
                    referred_device = d
                    break
            if not referred_device and history:
                prev_targets = self._carry_over(history, current_room)
                if prev_targets:
                    referred_device = prev_targets[0]

            # Đề xuất tắt thiết bị trước đó
            if is_turn_off_confirm and any(w in last_assistant_norm for w in ("tat den", "de ngu khong", "tat bot den", "tat di", "tat may lanh", "keo lai")):
                if referred_device:
                    power_cap = self._power_capability(referred_device.writable_capabilities)
                    if power_cap:
                        cmds = [self._cmd(referred_device, power_cap, self._power_value(power_cap, on=False))]
                        return {
                            "speech": f"Vâng, mình đã tắt {referred_device.name.lower()} cho bạn rồi nhé!",
                            "commands": cmds,
                            "scene": None,
                            "needs_clarification": False,
                        }
                target_room = current_room or "bedroom"
                lights = [d for d in self._home.devices if d.type is DeviceType.LIGHT and (d.room == target_room or not current_room)]
                if not lights:
                    lights = [d for d in self._home.devices if d.type is DeviceType.LIGHT]
                cmds = []
                for light in lights:
                    power_cap = self._power_capability(light.writable_capabilities)
                    if power_cap:
                        cmds.append(self._cmd(light, power_cap, self._power_value(power_cap, on=False)))
                return {
                    "speech": "Vâng, mình đã tắt đèn cho bạn rồi, chúc bạn ngủ thật ngon nhé!",
                    "commands": cmds,
                    "scene": None,
                    "needs_clarification": False,
                }

            # Đề xuất bật thiết bị trước đó
            if is_turn_on_confirm and any(w in last_assistant_norm for w in ("bat den", "cho sang khong", "sang khong", "bat len", "bat mat", "mo rem", "mo khoa")):
                if referred_device:
                    if referred_device.type is DeviceType.LIGHT:
                        cmds = self._set_light_comfort(referred_device, brightness=100)
                        return {
                            "speech": f"Dạ được rồi, mình đã bật {referred_device.name.lower()} lên cho bạn rồi nhé!",
                            "commands": cmds,
                            "scene": None,
                            "needs_clarification": False,
                        }
                    elif referred_device.type is DeviceType.AIR_CONDITIONER:
                        cmds = self._set_ac_comfort(referred_device, state, temp=25)
                        return {
                            "speech": f"Dạ, mình đã bật {referred_device.name.lower()} ở 25 độ cho bạn rồi nhé!",
                            "commands": cmds,
                            "scene": None,
                            "needs_clarification": False,
                        }
                    elif referred_device.type is DeviceType.CURTAIN:
                        pos_cap = self._power_capability(referred_device.writable_capabilities)
                        if pos_cap:
                            cmds = [self._cmd(referred_device, pos_cap, self._power_value(pos_cap, on=True))]
                            return {
                                "speech": f"Dạ, mình đã mở {referred_device.name.lower()} cho thoáng rồi nhé!",
                                "commands": cmds,
                                "scene": None,
                                "needs_clarification": False,
                            }
                    elif referred_device.type is DeviceType.LOCK:
                        lock_cap = referred_device.writable_capabilities.get("locked")
                        if lock_cap:
                            cmds = [self._cmd(referred_device, lock_cap, "false")]
                            return {
                                "speech": f"Dạ, mình đã mở khóa {referred_device.name.lower()} cho bạn rồi nhé!",
                                "commands": cmds,
                                "scene": None,
                                "needs_clarification": False,
                            }
                    else:
                        power_cap = self._power_capability(referred_device.writable_capabilities)
                        if power_cap:
                            cmds = [self._cmd(referred_device, power_cap, self._power_value(power_cap, on=True))]
                            return {
                                "speech": f"Dạ được rồi, mình đã bật {referred_device.name.lower()} cho bạn rồi nhé!",
                                "commands": cmds,
                                "scene": None,
                                "needs_clarification": False,
                            }

                target_room = current_room or "living_room"
                light = self._find_device_of_type(DeviceType.LIGHT, target_room)
                if light:
                    cmds = self._set_light_comfort(light, brightness=100)
                    return {
                        "speech": "Dạ được rồi, mình đã bật đèn lên cho bạn rồi nhé!",
                        "commands": cmds,
                        "scene": None,
                        "needs_clarification": False,
                    }

        # 2.2 Người dùng trả lời tên phòng sau khi trợ lý hỏi làm rõ (vd: "bật đèn" -> "phòng nào?" -> "phòng khách")
        matched_room = self._match_room(norm)
        if matched_room is not None and not self._has_action(norm):
            context_text = f"{last_assistant_norm} {prev_user_norm}"
            room_display = self._room_name(matched_room)
            if any(w in context_text for w in ("den", "light")):
                is_off = any(w in context_text for w in _OFF_WORDS)
                light = self._find_device_of_type(DeviceType.LIGHT, matched_room)
                if light:
                    power_cap = self._power_capability(light.writable_capabilities)
                    if power_cap:
                        val = self._power_value(power_cap, on=not is_off)
                        cmds = [self._cmd(light, power_cap, val)]
                        action_text = "tắt" if is_off else "bật"
                        return {
                            "speech": f"Dạ được rồi, mình đã {action_text} đèn {room_display} cho bạn rồi nhé!",
                            "commands": cmds,
                            "scene": None,
                            "needs_clarification": False,
                        }
            elif any(w in context_text for w in ("dieu hoa", "may lanh", "ac")):
                is_off = any(w in context_text for w in _OFF_WORDS)
                ac = self._find_device_of_type(DeviceType.AIR_CONDITIONER, matched_room)
                if ac:
                    power_cap = self._power_capability(ac.writable_capabilities)
                    if power_cap:
                        val = self._power_value(power_cap, on=not is_off)
                        cmds = [self._cmd(ac, power_cap, val)]
                        action_text = "tắt" if is_off else "bật"
                        return {
                            "speech": f"Dạ, mình đã {action_text} điều hòa {room_display} cho bạn rồi nhé!",
                            "commands": cmds,
                            "scene": None,
                            "needs_clarification": False,
                        }
            elif any(w in context_text for w in ("rem", "curtain")):
                is_close = any(w in context_text for w in ("dong", "tat", "khep"))
                curtain = self._find_device_of_type(DeviceType.CURTAIN, matched_room)
                if curtain:
                    power_cap = self._power_capability(curtain.writable_capabilities)
                    if power_cap:
                        val = self._power_value(power_cap, on=not is_close)
                        cmds = [self._cmd(curtain, power_cap, val)]
                        action_text = "đóng" if is_close else "mở"
                        return {
                            "speech": f"Dạ, mình đã {action_text} rèm {room_display} cho bạn rồi nhé!",
                            "commands": cmds,
                            "scene": None,
                            "needs_clarification": False,
                        }

        # -------------------------------------------------------
        # 3. TÍNH SUY LUẬN CHỦ ĐỘNG (Proactive Inference)
        # -------------------------------------------------------
        # 3.1 "Tôi buồn ngủ quá"
        if any(w in norm for w in ("buon ngu qua", "buon ngu roi", "buon ngu", "muon di ngu", "met qua muon di ngu")):
            return {
                "speech": "Bạn có cần mình tắt đèn cho dễ ngủ không?",
                "commands": [],
                "scene": None,
                "needs_clarification": True,
            }

        # 3.2 "Trời tối quá nhỉ" / "Tối quá"
        if any(w in norm for w in ("troi toi qua", "toi qua nhi", "sao toi the", "phong toi qua", "toi qua di", "toi the nhi")) and not self._has_action(norm):
            return {
                "speech": "Bạn có cần bật đèn cho sáng không?",
                "commands": [],
                "scene": None,
                "needs_clarification": True,
            }

        # -------------------------------------------------------
        # 4. KHỚP KỊCH BẢN (Scenes)
        # -------------------------------------------------------
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

        # -------------------------------------------------------
        # 5. CẢM GIÁC SINH HOẠT ĐỜI THƯỜNG (Nóng, Lạnh, Sáng quá)
        # -------------------------------------------------------
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

        # -------------------------------------------------------
        # 6. LÀM RÕ KHI THIẾU PHÒNG (Ambiguity Disambiguation: "Bật đèn", "Tắt đèn")
        # -------------------------------------------------------
        has_room_in_text = self._match_room(norm) is not None
        has_all_words = any(w in norm for w in _ALL_WORDS)
        if not has_room_in_text and not has_all_words and self._has_action(norm):
            words_in_norm = norm.split()
            if any(w in ("den", "bong den") for w in words_in_norm):
                is_off = any(w in norm for w in _OFF_WORDS)
                action_text = "tắt" if is_off else "bật"
                return {
                    "speech": f"Ý bạn là đang muốn {action_text} đèn ở phòng nào ạ? Phòng khách hay phòng ngủ?",
                    "commands": [],
                    "scene": None,
                    "needs_clarification": True,
                }
            if any(w in norm for w in ("dieu hoa", "may lanh")):
                is_off = any(w in norm for w in _OFF_WORDS)
                action_text = "tắt" if is_off else "bật"
                return {
                    "speech": f"Ý bạn là đang muốn {action_text} điều hòa ở phòng khách hay phòng ngủ ạ?",
                    "commands": [],
                    "scene": None,
                    "needs_clarification": True,
                }

        # -------------------------------------------------------
        # 7. KHỚP THIẾT BỊ VÀ THỰC THI LỆNH
        # -------------------------------------------------------
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
            return self._ask_device_intent(targets[0], state, norm)

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
    def _ask_device_intent(
        self, device: Device, state: dict[str, dict[str, str]], norm: str
    ) -> dict[str, Any]:
        current = state.get(device.id, {})
        dname = device.name.lower()
        power = str(current.get("power", current.get("state", "off"))).lower()
        is_on = power in ("on", "true", "1", "open")

        if device.type is DeviceType.LIGHT:
            brightness = current.get("brightness")
            if is_on:
                if brightness:
                    speech = f"Đèn {dname} hiện đang bật ở mức {brightness}% nè. Bạn có muốn mình tắt đi hay đổi độ sáng không?"
                else:
                    speech = f"Đèn {dname} đang sáng đó bạn. Bạn muốn mình tắt đi hay chỉnh độ sáng nè?"
            else:
                speech = f"Đèn {dname} hiện đang tắt. Bạn có muốn mình bật lên cho sáng không nè?"

        elif device.type is DeviceType.AIR_CONDITIONER:
            temp = current.get("temperature", current.get("target_temperature", "25"))
            if is_on:
                speech = f"Điều hòa {dname} đang chạy ở {temp} độ nè. Bạn muốn mình tăng giảm nhiệt độ hay tắt máy lạnh đi ạ?"
            else:
                speech = f"Điều hòa {dname} hiện đang tắt. Bạn có muốn mình bật mát ở 25 độ không nè?"

        elif device.type is DeviceType.CURTAIN:
            pos = str(current.get("position", current.get("state", "closed"))).lower()
            if is_on or pos in ("open", "100"):
                speech = f"Rèm {dname} hiện đang mở đón ánh sáng. Bạn có muốn mình kéo lại cho râm mát không?"
            else:
                speech = f"Rèm {dname} đang đóng kín. Bạn có muốn mình mở rèm ra cho thoáng không nè?"

        elif device.type is DeviceType.LOCK:
            locked = str(current.get("locked", "true")).lower() in ("true", "1", "locked")
            if locked:
                speech = f"Khóa {dname} hiện đang khóa an toàn. Bạn có cần mình mở khóa không ạ?"
            else:
                speech = f"Khóa {dname} đang mở đó bạn ơi. Bạn có muốn mình khóa lại cho an toàn không?"

        elif device.type in (DeviceType.TV, DeviceType.SPEAKER):
            if is_on:
                speech = f"{device.name} đang mở nè. Bạn muốn mình điều chỉnh âm lượng hay tắt đi không?"
            else:
                speech = f"{device.name} hiện đang tắt. Bạn có muốn mình bật lên để bạn giải trí không nè?"

        elif device.type is DeviceType.WATER_HEATER:
            if is_on:
                speech = "Bình nước nóng đang bật đó bạn. Bạn muốn mình tắt đi hay để đun tiếp nè?"
            else:
                speech = "Bình nóng lạnh hiện đang tắt. Bạn có muốn mình bật lên để chuẩn bị nước ấm không?"

        elif device.type is DeviceType.FAN:
            if is_on:
                speech = "Quạt đang chạy nè. Bạn muốn mình tăng giảm gió hay tắt quạt đi ạ?"
            else:
                speech = "Quạt hiện đang tắt. Bạn có muốn mình bật quạt cho mát không nè?"

        else:
            if is_on:
                speech = f"{device.name} hiện đang bật hoạt động. Bạn có muốn mình tắt đi không nè?"
            else:
                speech = f"{device.name} hiện đang tắt. Bạn có muốn mình bật lên không?"

        return {
            "speech": speech,
            "commands": [],
            "scene": None,
            "needs_clarification": True,
        }

    def _answer_query(
        self, targets: list[Device], state: dict[str, dict[str, str]]
    ) -> dict[str, Any]:
        if not targets:
            return {
                "speech": "Bạn muốn hỏi về thiết bị nào ạ? Đèn, điều hòa hay rèm cửa nè?",
                "commands": [],
                "scene": None,
                "needs_clarification": True,
            }
        parts: list[str] = []
        for device in targets[:3]:
            values = state.get(device.id, {})
            if not values:
                parts.append(f"{device.name} chưa có dữ liệu gửi về")
                continue
            power = str(values.get("power", values.get("state", "off"))).lower()
            is_on = power in ("on", "true", "1", "open")

            if device.type is DeviceType.LIGHT:
                bright = values.get("brightness")
                if is_on:
                    b_str = f" ở độ sáng {bright}%" if bright else ""
                    parts.append(f"{device.name} đang bật{b_str}")
                else:
                    parts.append(f"{device.name} đang tắt")
            elif device.type is DeviceType.AIR_CONDITIONER:
                temp = values.get("temperature", values.get("target_temperature", "25"))
                if is_on:
                    parts.append(f"{device.name} đang bật ở {temp} độ")
                else:
                    parts.append(f"{device.name} đang tắt")
            elif device.type is DeviceType.CURTAIN:
                pos = str(values.get("position", values.get("state", "closed"))).lower()
                if is_on or pos in ("open", "100"):
                    parts.append(f"{device.name} đang mở")
                else:
                    parts.append(f"{device.name} đang đóng")
            elif device.type is DeviceType.LOCK:
                locked = str(values.get("locked", "true")).lower() in ("true", "1", "locked")
                parts.append(f"{device.name} đang {'khóa an toàn' if locked else 'mở'}")
            else:
                rendered = ", ".join(f"{k} {v}" for k, v in list(values.items())[:2])
                parts.append(f"{device.name} ({rendered})")

        return {
            "speech": "Dạ, hiện tại " + ", và ".join(parts) + " bạn nhé.",
            "commands": [],
            "scene": None,
            "needs_clarification": False,
        }

    def _room_name(self, room_id: str) -> str:
        room = self._home.room_map.get(room_id)
        return room.name.lower() if room else room_id

    @staticmethod
    def _describe(targets: list[Device], commands: list[dict[str, Any]]) -> str:
        names = ", ".join(dict.fromkeys(d.name for d in targets))
        lock = next((c for c in commands if c["capability"] == "locked"), None)
        if lock is not None:
            return (
                f"Dạ, mình đã khóa an toàn {names} rồi bạn nhé."
                if lock["value"] == "true"
                else f"Dạ, mình đã mở khóa {names} cho bạn rồi nhé."
            )
        powered_off = any(
            c["capability"] in {"power", "state", "on", "open"}
            and str(c["value"]).lower() in {"off", "false", "closed", "đóng", "tắt"}
            for c in commands
        )
        if powered_off:
            variations = [
                f"Dạ được rồi, mình đã tắt {names} cho bạn rồi nhé.",
                f"Mình vừa tắt {names} giúp bạn rồi đó nha.",
                f"Xong rồi nè, {names} đã được tắt rồi bạn nhé.",
                f"Đã tắt {names} giúp bạn rồi nha.",
            ]
            idx = sum(ord(c) for c in names) % len(variations)
            return variations[idx]

        levels = [c for c in commands if c["capability"] not in {"power", "state", "on", "open"}]
        if levels:
            bright = next((c["value"] for c in levels if c["capability"] == "brightness"), None)
            if bright is not None:
                return f"Dạ, mình đã chỉnh độ sáng {names} ở mức {bright}% rồi bạn nhé."
            temp = next((c["value"] for c in levels if c["capability"] in ("temperature", "target_temperature", "setpoint")), None)
            if temp is not None:
                return f"Dạ, mình đã đặt nhiệt độ {names} ở {temp} độ cho bạn rồi nhé."
            detail = ", ".join(f"{c['capability']} {c['value']}" for c in levels[:2])
            return f"Dạ, mình đã điều chỉnh {names}: {detail} cho bạn rồi nhé."

        variations = [
            f"Dạ được rồi, mình đã bật {names} cho bạn rồi nhé!",
            f"Mình vừa bật {names} giúp bạn rồi đó nha.",
            f"Xong rồi nè, {names} đã được bật lên rồi ạ.",
            f"Dạ, {names} đã sẵn sàng hoạt động rồi bạn nhé!",
        ]
        idx = sum(ord(c) for c in names) % len(variations)
        return variations[idx]

