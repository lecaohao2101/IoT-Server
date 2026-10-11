"""Tests for conversational intelligence, proactive reasoning, multi-turn context, and empathy."""

from __future__ import annotations

from app.services.orchestrator import TurnSink


class CollectingSink(TurnSink):
    def __init__(self) -> None:
        self.deltas: list[str] = []

    async def assistant_delta(self, text: str) -> None:
        self.deltas.append(text)

    @property
    def speech(self) -> str:
        return "".join(self.deltas)


async def test_proactive_reasoning_sleepy_flow(container):
    """User expresses fatigue/sleepiness -> assistant suggests turning off lights -> user confirms -> lights turn off."""
    orchestrator = container.orchestrator
    session_id = "test-sleepy-session"

    # Turn 1: Proactive inference
    res1 = await orchestrator.run_turn(
        session_id=session_id, user_text="tôi buồn ngủ quá", speak=False
    )
    assert res1.needs_clarification is True
    assert "tắt đèn cho dễ ngủ không" in res1.speech
    assert res1.plan is None or not res1.plan.accepted

    # Turn 2: User confirms
    res2 = await orchestrator.run_turn(
        session_id=session_id, user_text="ừ tắt đi", speak=False
    )
    assert res2.needs_clarification is False
    assert res2.plan is not None and res2.plan.accepted
    assert any(c.capability == "power" and c.value == "off" for c in res2.plan.accepted)
    assert "chúc bạn ngủ" in res2.speech.lower() or "tắt đèn" in res2.speech.lower()


async def test_proactive_reasoning_darkness_flow(container):
    """User remarks it's too dark -> assistant suggests turning on lights -> user confirms -> lights turn on."""
    orchestrator = container.orchestrator
    session_id = "test-darkness-session"

    # Turn 1: Proactive inference
    res1 = await orchestrator.run_turn(
        session_id=session_id, user_text="trời tối quá nhỉ", speak=False
    )
    assert res1.needs_clarification is True
    assert "bật đèn cho sáng không" in res1.speech
    assert res1.plan is None or not res1.plan.accepted

    # Turn 2: User confirms
    res2 = await orchestrator.run_turn(
        session_id=session_id, user_text="có bật đi", speak=False
    )
    assert res2.needs_clarification is False
    assert res2.plan is not None and res2.plan.accepted
    assert any(c.capability == "power" and c.value == "on" for c in res2.plan.accepted)


async def test_multi_turn_room_disambiguation_flow(container):
    """User says 'bật đèn' without room -> assistant asks which room -> user answers 'phòng khách' -> living room light turns on."""
    orchestrator = container.orchestrator
    session_id = "test-disambiguate-session"

    # Turn 1: Underspecified command
    res1 = await orchestrator.run_turn(
        session_id=session_id, user_text="bật đèn", speak=False
    )
    assert res1.needs_clarification is True
    assert "phòng nào" in res1.speech
    assert res1.plan is None or not res1.plan.accepted

    # Turn 2: User specifies room
    res2 = await orchestrator.run_turn(
        session_id=session_id, user_text="phòng khách", speak=False
    )
    assert res2.needs_clarification is False
    assert res2.plan is not None and res2.plan.accepted
    assert any(c.device_id == "living_room_light" and c.value == "on" for c in res2.plan.accepted)
    assert "phòng khách" in res2.speech.lower()


async def test_human_empathy_and_smalltalk(container):
    """Assistant speaks warmly like two human beings living together."""
    orchestrator = container.orchestrator

    # 1. Fatigue check-in
    res_tired = await orchestrator.run_turn(
        session_id="test-empathy-1", user_text="hôm nay đi làm mệt quá", speak=False
    )
    assert res_tired.needs_clarification is True
    assert "thương bạn ghê" in res_tired.speech.lower() or "nghỉ ngơi" in res_tired.speech.lower()

    # 2. Gratitude
    res_thanks = await orchestrator.run_turn(
        session_id="test-empathy-2", user_text="cảm ơn bạn nhiều nhé", speak=False
    )
    assert res_thanks.needs_clarification is False
    assert any(phrase in res_thanks.speech.lower() for phrase in ("không có chi", "hỗ trợ bạn", "có gì đâu", "niềm vui"))

    # 3. Identity
    res_who = await orchestrator.run_turn(
        session_id="test-empathy-3", user_text="bạn là ai thế", speak=False
    )
    assert "trợ lý thông minh" in res_who.speech.lower()


async def test_device_mentioned_without_command_situational_awareness(container):
    """When a device is mentioned with no action verb, assistant inspects its current state and asks intelligently."""
    orchestrator = container.orchestrator
    session_id = "test-device-situational-session"

    # Turn 1: User mentions "đèn phòng khách" without action.
    # The seeded state of living_room_light is OFF.
    res1 = await orchestrator.run_turn(
        session_id=session_id, user_text="đèn phòng khách", speak=False
    )
    assert res1.needs_clarification is True
    # Must NOT be the old robotic template: "Bạn muốn mình bật, tắt hay chỉnh gì cho đèn phòng khách ạ?"
    assert "bạn muốn mình bật, tắt hay chỉnh gì" not in res1.speech.lower()
    # Must intelligently recognize that it is currently OFF and propose turning it on:
    assert "hiện đang tắt" in res1.speech.lower() or "đang tắt" in res1.speech.lower()
    assert "bật lên" in res1.speech.lower() or "bật" in res1.speech.lower()

    # Turn 2: User says "ừ bật đi" -> Assistant executes command to turn it on!
    res2 = await orchestrator.run_turn(
        session_id=session_id, user_text="ừ bật đi", speak=False
    )
    assert res2.needs_clarification is False
    assert res2.plan is not None and res2.plan.accepted
    assert any(c.device_id == "living_room_light" and c.value == "on" for c in res2.plan.accepted)
    assert "đèn phòng khách" in res2.speech.lower()

