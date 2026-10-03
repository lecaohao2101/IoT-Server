"""Orchestrator, state manager and MQTT bridge, driven with scripted model output.

These tests pin the behaviour that matters when the model misbehaves: a malformed
answer, a provider outage, a command the policy refuses, and a sensitive action
that must wait for a human.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.ai.base import AudioEncoding, LanguageModel, LlmMessage
from app.core.errors import ProviderError
from app.domain.commands import Command
from app.mqtt.topics import decode_state_payload, encode_command
from app.services.orchestrator import FALLBACK_SPEECH, Orchestrator, TurnSink


class ScriptedLlm(LanguageModel):
    """Replays a fixed response, optionally failing part-way through."""

    name = "scripted"

    def __init__(self, payload: Any, *, chunk: int = 16, fail_after: int | None = None) -> None:
        self.text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        self.chunk = chunk
        self.fail_after = fail_after
        self.calls: list[list[LlmMessage]] = []
        self.last_system = ""

    async def stream(
        self, *, system: str, messages: list[LlmMessage], json_schema: dict | None = None
    ) -> AsyncIterator[str]:
        self.calls.append(messages)
        self.last_system = system
        for emitted, start in enumerate(range(0, len(self.text), self.chunk)):
            if self.fail_after is not None and emitted >= self.fail_after:
                raise ProviderError("upstream exploded")
            yield self.text[start : start + self.chunk]


class RecordingSink(TurnSink):
    def __init__(self) -> None:
        self.deltas: list[str] = []
        self.audio: list[bytes] = []
        self.notices: list[tuple[str, str]] = []
        self.started = False
        self.ended = False
        self.first_delta = asyncio.Event()

    async def assistant_delta(self, text: str) -> None:
        self.deltas.append(text)
        self.first_delta.set()

    async def audio_start(self, encoding: AudioEncoding, sample_rate: int) -> None:
        self.started = True

    async def audio_chunk(self, payload: bytes) -> None:
        self.audio.append(payload)

    async def audio_end(self) -> None:
        self.ended = True

    async def notice(self, code: str, message: str) -> None:
        self.notices.append((code, message))

    @property
    def speech(self) -> str:
        return "".join(self.deltas)


def orchestrator_with(container, llm: LanguageModel) -> Orchestrator:
    return Orchestrator(
        home=container.home,
        policy=container.policy,
        llm=llm,
        tts=container.tts,
        validator=container.validator,
        state=container.state,
        conversations=container.conversations,
        bridge=container.bridge,
        bus=container.bus,
    )


# ------------------------------------------------------------------ happy path


async def test_turn_speaks_and_publishes_one_grouped_mqtt_message(container):
    llm = ScriptedLlm(
        {
            "speech": "Đã bật đèn phòng khách ở mức 70%.",
            "commands": [
                {"device_id": "living_room_light", "capability": "power", "value": "on"},
                {"device_id": "living_room_light", "capability": "brightness", "value": "70"},
            ],
        }
    )
    sink = RecordingSink()
    result = await orchestrator_with(container, llm).run_turn(
        session_id="s1", user_text="bật đèn phòng khách 70%", sink=sink
    )

    assert result.speech.startswith("Đã bật đèn phòng khách")
    assert sink.speech == "Đã bật đèn phòng khách ở mức 70%."
    assert sink.started and sink.ended and sink.audio

    # Two capabilities on one device must travel as a single message.
    sent = [(t, p) for t, p in container.transport.sent if t.endswith("/set")]
    assert len(sent) == 1
    topic, payload = sent[0]
    assert topic == "home/living_room/living_room_light/set"
    assert decode_state_payload(payload) == {"power": "on", "brightness": 70}


async def test_speech_is_streamed_before_the_commands_are_parsed(container):
    """The point of the streaming extractor: audio starts mid-JSON."""
    payload = json.dumps(
        {
            "speech": "Đã bật đèn.",
            "commands": [{"device_id": "kitchen_light", "capability": "power", "value": "on"}],
        },
        ensure_ascii=False,
    )
    split = payload.index('"commands"')

    class SlowTail(ScriptedLlm):
        async def stream(self, *, system, messages, json_schema=None):
            yield payload[:split]
            await asyncio.sleep(0.05)
            yield payload[split:]

    sink = RecordingSink()
    orchestrator = orchestrator_with(container, SlowTail(payload))
    task = asyncio.create_task(
        orchestrator.run_turn(session_id="s2", user_text="bật đèn bếp", sink=sink)
    )
    await asyncio.wait_for(sink.first_delta.wait(), timeout=1.0)
    assert sink.speech == "Đã bật đèn."
    assert not task.done(), "speech was delivered before the model finished"
    await task


async def test_state_is_marked_desired_then_confirmed_by_the_echo(container):
    llm = ScriptedLlm(
        {
            "speech": "Xong.",
            "commands": [{"device_id": "bedroom_light", "capability": "power", "value": "on"}],
        }
    )
    await orchestrator_with(container, llm).run_turn(session_id="s3", user_text="bật đèn ngủ")

    for _ in range(60):
        state = await container.state.get("bedroom_light")
        if state.attributes.get("power") == "on":
            break
        await asyncio.sleep(0.02)
    assert state.attributes["power"] == "on"
    assert state.desired == {}, "a confirmed value must stop being 'desired'"
    assert state.online


# ------------------------------------------------------------- model misbehaves


async def test_malformed_model_output_still_speaks_and_sends_nothing(container):
    llm = ScriptedLlm('{"speech": "Mình đang xử lý", "commands": [BROKEN')
    before = len(container.transport.sent)
    result = await orchestrator_with(container, llm).run_turn(
        session_id="s4", user_text="bật đèn"
    )
    assert result.speech == "Mình đang xử lý"
    assert result.plan is None
    assert len(container.transport.sent) == before


async def test_provider_failure_falls_back_to_an_apology(container):
    llm = ScriptedLlm({"speech": "x", "commands": []}, chunk=4, fail_after=0)
    sink = RecordingSink()
    result = await orchestrator_with(container, llm).run_turn(
        session_id="s5", user_text="bật đèn", sink=sink
    )
    assert result.speech == FALLBACK_SPEECH
    assert result.error
    assert ("llm_error", "upstream exploded") in sink.notices


async def test_invented_device_is_refused_and_explained_out_loud(container):
    llm = ScriptedLlm(
        {
            "speech": "Đã bật đèn gara.",
            "commands": [{"device_id": "garage_light", "capability": "power", "value": "on"}],
        }
    )
    result = await orchestrator_with(container, llm).run_turn(
        session_id="s6", user_text="bật đèn gara"
    )
    assert result.plan.rejected
    assert "garage_light" in result.plan.rejected[0].message
    assert result.plan.rejected[0].message in result.speech


async def test_clamped_value_is_mentioned_in_the_reply(container):
    llm = ScriptedLlm(
        {
            "speech": "Đã chỉnh điều hòa.",
            "commands": [
                {"device_id": "bedroom_ac", "capability": "temperature", "value": "10"}
            ],
        }
    )
    result = await orchestrator_with(container, llm).run_turn(
        session_id="s7", user_text="điều hòa 10 độ"
    )
    assert result.plan.accepted[0].value == 18
    assert "Lưu ý" in result.speech


# ------------------------------------------------------------------ confirmation


async def test_sensitive_action_waits_for_a_spoken_yes(container):
    llm = ScriptedLlm(
        {
            "speech": "Mình sẽ mở khóa cửa.",
            "commands": [
                {"device_id": "front_door_lock", "capability": "locked", "value": "false"}
            ],
        }
    )
    orchestrator = orchestrator_with(container, llm)

    first = await orchestrator.run_turn(session_id="s8", user_text="mở khóa cửa")
    assert first.plan.pending_confirmation
    assert not first.plan.accepted
    ctx = await container.conversations.get("s8")
    assert ctx.pending is not None

    before = len(container.transport.sent)
    second = await orchestrator.run_turn(session_id="s8", user_text="vâng")
    assert second.plan.accepted[0].device_id == "front_door_lock"
    assert len(container.transport.sent) > before
    assert (await container.conversations.get("s8")).pending is None
    assert len(llm.calls) == 1, "the confirmation must not reach the model"


async def test_declining_a_sensitive_action_cancels_it(container):
    llm = ScriptedLlm(
        {
            "speech": "Mình sẽ mở khóa cửa.",
            "commands": [
                {"device_id": "front_door_lock", "capability": "locked", "value": "false"}
            ],
        }
    )
    orchestrator = orchestrator_with(container, llm)
    await orchestrator.run_turn(session_id="s9", user_text="mở khóa cửa")

    before = len(container.transport.sent)
    result = await orchestrator.run_turn(session_id="s9", user_text="thôi")
    assert "huỷ" in result.speech.lower()
    assert len(container.transport.sent) == before
    assert (await container.conversations.get("s9")).pending is None


async def test_an_unrelated_reply_leaves_the_confirmation_pending(container):
    llm = ScriptedLlm(
        {
            "speech": "Mình sẽ mở khóa cửa.",
            "commands": [
                {"device_id": "front_door_lock", "capability": "locked", "value": "false"}
            ],
        }
    )
    orchestrator = orchestrator_with(container, llm)
    await orchestrator.run_turn(session_id="s10", user_text="mở khóa cửa")
    await orchestrator.run_turn(session_id="s10", user_text="mấy giờ rồi")
    assert len(llm.calls) == 2


# ------------------------------------------------------------------- context


async def test_prompt_carries_the_catalogue_and_live_state(container):
    await container.state.apply_reported("balcony_sensor", {"temperature": 33})
    llm = ScriptedLlm({"speech": "33 độ.", "commands": []})
    await orchestrator_with(container, llm).run_turn(
        session_id="s11", user_text="ngoài ban công bao nhiêu độ", room="living_room"
    )
    system = llm.last_system
    assert "balcony_sensor" in system
    assert "temperature=33" in system
    assert "Phòng khách (living_room)" in system


async def test_history_is_replayed_to_the_model(container):
    llm = ScriptedLlm({"speech": "ok", "commands": []})
    orchestrator = orchestrator_with(container, llm)
    await orchestrator.run_turn(session_id="s12", user_text="bật đèn bếp")
    await orchestrator.run_turn(session_id="s12", user_text="sáng hơn chút")
    assert [m.content for m in llm.calls[1]][-3:] == [
        "bật đèn bếp",
        "ok",
        "sáng hơn chút",
    ]


async def test_history_is_trimmed_to_the_configured_window(container):
    llm = ScriptedLlm({"speech": "ok", "commands": []})
    orchestrator = orchestrator_with(container, llm)
    for i in range(10):
        await orchestrator.run_turn(session_id="s13", user_text=f"câu {i}")
    ctx = await container.conversations.get("s13")
    assert len(ctx.turns) <= container.settings.conversation_max_turns


# ----------------------------------------------------------------- state layer


async def test_unknown_attributes_from_firmware_are_discarded(container):
    await container.state.apply_reported(
        "living_room_light", {"power": "on", "rssi": -70, "firmware": "1.2.3"}
    )
    state = await container.state.get("living_room_light")
    assert state.attributes["power"] == "on"
    assert "rssi" not in state.attributes


async def test_reported_values_are_coerced_to_the_declared_type(container):
    await container.state.apply_reported("living_room_light", {"brightness": "55"})
    assert (await container.state.get("living_room_light")).attributes["brightness"] == 55


async def test_availability_updates_are_published(container):
    seen: list[dict] = []

    async def listen():
        async for event in container.bus.subscribe("device.availability"):
            seen.append(event.payload)
            return

    task = asyncio.create_task(listen())
    await asyncio.sleep(0.01)
    await container.state.set_availability("kitchen_light", True)
    await asyncio.wait_for(task, timeout=1.0)
    assert seen[0] == {"device_id": "kitchen_light", "online": True}


async def test_corrupt_stored_state_is_ignored_not_fatal(container):
    await container.store.set("state:kitchen_light", "{{{not json")
    state = await container.state.get("kitchen_light")
    assert state.device_id == "kitchen_light"
    assert state.attributes == {}


# ---------------------------------------------------------------- mqtt bridge


async def test_inbound_state_topic_updates_the_model(container):
    await container.bridge.handle_message(
        "home/bedroom/bedroom_ac/state", b'{"power": "on", "temperature": 24}'
    )
    state = await container.state.get("bedroom_ac")
    # Reported values are merged over the seeded defaults, not a replacement.
    assert state.attributes["power"] == "on"
    assert state.attributes["temperature"] == 24
    assert state.attributes["mode"] == "cool"
    assert state.online


async def test_inbound_per_capability_topic_is_understood(container):
    await container.bridge.handle_message("home/kitchen/kitchen_outlet/state/power", b"true")
    assert (await container.state.get("kitchen_outlet")).attributes["power"] is True


async def test_availability_topic_marks_a_device_offline(container):
    await container.bridge.handle_message(
        "home/kitchen/kitchen_light/availability", b"offline"
    )
    assert (await container.state.get("kitchen_light")).online is False


async def test_unmapped_topic_is_ignored(container):
    await container.bridge.handle_message("home/attic/ghost/state", b'{"power": "on"}')
    # Nothing to assert beyond "did not raise"; the device does not exist.


async def test_delayed_command_is_scheduled_not_awaited(container):
    plan = container.validator.validate(
        [Command(device_id="kitchen_light", capability="power", value="on", delay_s=0.1)]
    )
    before = len(container.transport.sent)
    report = await container.bridge.dispatch(plan)
    assert report.delivered_count == 1
    assert len(container.transport.sent) == before, "delayed command must not publish yet"

    await asyncio.sleep(0.25)
    assert len(container.transport.sent) > before


def test_command_payload_is_compact_and_self_describing():
    payload = encode_command(
        device_id="x", values={"power": "on"}, plan_id="plan_1", delay_s=0
    )
    decoded = json.loads(payload)
    assert decoded["set"] == {"power": "on"}
    assert decoded["plan_id"] == "plan_1"
    assert "delay_s" not in decoded


# ------------------------------------------------------------------- direct API


async def test_execute_commands_uses_the_same_validator(container):
    plan, report = await container.orchestrator.execute_commands(
        [Command(device_id="bedroom_ac", capability="temperature", value="5")]
    )
    assert plan.accepted[0].value == 18
    assert report.delivered_count == 1


async def test_empty_text_turn_is_a_no_op(container):
    result = await container.orchestrator.run_turn(session_id="s14", user_text="   ")
    assert result.plan is None
    assert result.speech


@pytest.mark.parametrize("text", ["bật đèn phòng khách", "tắt hết đèn", "mở rèm phòng ngủ"])
async def test_offline_reasoner_handles_common_phrasings(container, text):
    result = await container.orchestrator.run_turn(session_id="s15", user_text=text)
    assert result.plan is not None and result.plan.accepted, text


async def test_offline_reasoner_carries_the_subject_across_turns(container):
    """"giảm xuống 20%" names no device: the previous turn must supply it."""
    orchestrator = container.orchestrator
    first = await orchestrator.run_turn(
        session_id="s16", user_text="bật đèn phòng khách lên 70 phần trăm"
    )
    assert first.plan.accepted[0].device_id == "living_room_light"

    second = await orchestrator.run_turn(session_id="s16", user_text="giảm xuống 20 phần trăm")
    assert second.plan.accepted[0].device_id == "living_room_light"
    assert second.plan.accepted[0].value == 20

    third = await orchestrator.run_turn(session_id="s16", user_text="tắt đi")
    assert third.plan.accepted[0].capability == "power"
    assert third.plan.accepted[0].value == "off"


async def test_offline_reasoner_unlocks_and_locks_the_door(container):
    """"mở khóa" must not be read as "bật": locks invert the usual vocabulary."""
    unlock = await container.orchestrator.run_turn(
        session_id="s17", user_text="mở khóa cửa chính"
    )
    assert unlock.plan.pending_confirmation[0].device_id == "front_door_lock"
    assert unlock.plan.pending_confirmation[0].value is False
    assert not unlock.plan.accepted

    lock = await container.orchestrator.run_turn(session_id="s18", user_text="khóa cửa lại")
    assert lock.plan.pending_confirmation[0].value is True


async def test_cancelling_a_turn_does_not_leak_the_synthesis_task(container):
    """Barge-in cancels the turn mid-reply.

    The TTS worker runs as its own task waiting on a queue; if the turn is
    cancelled before `finish()` puts the sentinel in, that task waits forever.
    One leaked task per interruption, for the life of the process.
    """

    class SlowLlm(LanguageModel):
        name = "slow"

        async def stream(self, *, system, messages, json_schema=None):
            yield '{"speech": "Đang nói một câu dài. '
            await asyncio.sleep(10)  # user cuts in here
            yield 'phần còn lại", "commands": []}'

    orchestrator = orchestrator_with(container, SlowLlm())
    task = asyncio.create_task(
        orchestrator.run_turn(session_id="barge", user_text="xin chào", sink=RecordingSink())
    )
    await asyncio.sleep(0.2)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.1)

    lingering = [t for t in asyncio.all_tasks() if t.get_name() == "tts-worker" and not t.done()]
    assert lingering == [], f"{len(lingering)} tts-worker còn treo"


async def test_a_normal_turn_leaves_no_background_tasks(container):
    before = {t for t in asyncio.all_tasks() if not t.done()}
    await container.orchestrator.run_turn(session_id="clean", user_text="bật đèn bếp")
    await asyncio.sleep(0.1)
    leaked = {
        t
        for t in asyncio.all_tasks()
        if not t.done() and t not in before and t.get_name() in {"tts-worker", "turn"}
    }
    assert leaked == set()
