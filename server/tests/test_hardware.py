"""Tests for ESP32 hardware compatibility routes."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient


def test_update_status_updates_leds_and_sensors(client: TestClient) -> None:
    payload = {
        "buttons": {"living": True},
        "leds": {
            "lr_main": True,
            "lr_sofa": True,
            "kit_main": False,
            "bed_main": True,
            "bed_side": True,
            "study": False,
            "balcony": True,
            "wc": True,
        },
        "servos": {
            "lr_curtain": 60,
            "bed_curtain": 0,
        },
        "sensors": {
            "temp": 28.5,
            "hum": 65.0,
            "sound_level": 45,
            "distance_cm": 25.0,
        },
    }

    res = client.post("/update-status", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "living_room_light" in data["updated"]
    assert "living_room_curtain" in data["updated"]
    assert "living_room_sensor" in data["updated"]
    assert "bedroom_sensor" in data["updated"]

    # Verify returned hardware pin states
    assert data["states"]["lr_main"] == 1
    assert data["states"]["lr_sofa"] == 1
    assert data["states"]["kit_main"] == 0
    assert data["states"]["bed_main"] == 1
    assert data["states"]["bed_side"] == 1
    assert data["states"]["study"] == 0
    assert data["states"]["balcony"] == 1
    assert data["states"]["wc"] == 1
    assert data["states"]["lr_curtain"] == 60
    assert data["states"]["bed_curtain"] == 0

    # Verify returned room-based structure
    assert "rooms" in data
    assert data["rooms"]["living_room"]["main_light"] == 1
    assert data["rooms"]["living_room"]["sofa_reading_light"] == 1
    assert data["rooms"]["kitchen"]["main_light"] == 0
    assert data["rooms"]["bedroom"]["main_light"] == 1

    # Verify device state endpoints reflect the hardware report
    lr_light = client.get("/api/v1/devices/living_room_light").json()
    assert lr_light["state"]["power"] == "on"

    # Both servos are curtains; the air conditioners move nothing at all, so a
    # servo report must never be read back as an AC that turned itself on.
    lr_curtain = client.get("/api/v1/devices/living_room_curtain").json()
    assert lr_curtain["state"]["position"] == 60
    assert lr_curtain["state"]["state"] == "open"

    bed_curtain = client.get("/api/v1/devices/bedroom_curtain").json()
    assert bed_curtain["state"]["state"] == "closed"

    assert client.get("/api/v1/devices/living_room_ac").json()["state"]["power"] == "off"

    lr_sensor = client.get("/api/v1/devices/living_room_sensor").json()
    assert lr_sensor["state"]["temperature"] == 28.5
    assert lr_sensor["state"]["humidity"] == 65.0
    assert lr_sensor["state"]["sound_level"] == 45

    bed_sensor = client.get("/api/v1/devices/bedroom_sensor").json()
    assert bed_sensor["state"]["distance_cm"] == 25.0


def test_poll_commands_returns_current_pin_states(client: TestClient) -> None:
    res = client.get("/poll-commands")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "lr_main" in data["states"]
    assert "lr_curtain" in data["states"]


def test_an_open_curtain_reaches_the_board_as_a_position(client: TestClient) -> None:
    """The two servos are the curtains, and the board steers them by percent.

    "mo rem" carries no number, so the position left over from the last run
    would keep the servo shut if the route simply echoed it back.
    """
    res = client.post(
        "/api/v1/devices/living_room_curtain/command",
        json={"capability": "state", "value": "open"},
    )
    assert res.status_code == 202

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        states = client.get("/poll-commands").json()["states"]
        if states["lr_curtain"] > 0:
            break
        time.sleep(0.05)
    assert states["lr_curtain"] == 100

    client.post(
        "/api/v1/devices/living_room_curtain/command",
        json={"capability": "position", "value": 40},
    )
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        states = client.get("/poll-commands").json()["states"]
        if states["lr_curtain"] == 40:
            break
        time.sleep(0.05)
    assert states["lr_curtain"] == 40


def test_the_air_conditioner_moves_no_hardware(client: TestClient) -> None:
    """It is a number on an LCD, nothing else -- no servo answers to it.

    Both servos belong to the curtains now, so an AC command that still moved
    one would swing a curtain every time somebody asked for 24 degrees.
    """
    before = client.get("/poll-commands").json()["states"]

    res = client.post(
        "/api/v1/devices/living_room_ac/command",
        json={"capability": "temperature", "value": 24},
    )
    assert res.status_code == 202

    after = client.get("/poll-commands").json()["states"]
    assert after["lr_curtain"] == before["lr_curtain"]
    assert after["bed_curtain"] == before["bed_curtain"]

    # And the vane the servo used to stand for is gone from the catalogue.
    ac = client.get("/api/v1/devices/living_room_ac").json()
    assert "vane_angle" not in ac["state"]


def test_upload_audio_short_payload_rejected(client: TestClient) -> None:
    res = client.post("/upload-audio", content=b"short")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is False
    assert data["error"] == "empty_audio"


def test_upload_audio_executes_voice_command(client: TestClient) -> None:
    # Under mock STT, valid UTF-8 prose becomes the recognized transcript
    audio_text = "Anh Thạc Sĩ, bật đèn phòng khách".encode()
    padding = b" " * 120  # ensure > 100 bytes
    res = client.post("/upload-audio", content=audio_text + padding)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "transcript" in data
    assert "response" in data
    assert "states" in data


def test_upload_audio_survives_an_stt_provider_failure(client: TestClient) -> None:
    """A provider error must come back as JSON the firmware can read, not a 500."""
    from app.ai.base import SttStream
    from app.core.errors import ProviderError

    class FailingStream(SttStream):
        async def push(self, pcm: bytes) -> None: ...

        async def end_of_audio(self) -> None: ...

        async def results(self):
            raise ProviderError("Google STT stream failed: 400 chunk too large")
            yield  # pragma: no cover - makes this an async generator

        async def aclose(self) -> None: ...

    container = client.app.state.container
    original = container.stt.open_stream
    container.stt.open_stream = lambda **kwargs: FailingStream()
    try:
        res = client.post("/upload-audio", content=b"\x01\x02" * 64)
    finally:
        container.stt.open_stream = original

    assert res.status_code == 200
    data = res.json()
    assert data["success"] is False
    assert data["error"] == "stt_failed"
    assert data["response"]


def test_upload_audio_ignores_a_sentence_not_addressed_to_the_assistant(
    client: TestClient,
) -> None:
    """The ESP32 route takes no credential, so the gate is the only guard on it.

    A passer-by saying "bật đèn phòng khách" near the balcony mic must get the
    same silence a stranger's voice gets, and the lights must not move.
    """
    before = client.get("/api/v1/devices/living_room_light").json()["state"]["power"]

    res = client.post("/upload-audio", content="bật đèn phòng khách".encode() + b" " * 120)

    assert res.status_code == 200, "the firmware only reads JSON out of a 200"
    data = res.json()
    assert data["success"] is False
    assert data["error"] == "wake_word_required"
    assert data["response"] == "", "nothing may be spoken at an overheard sentence"
    assert data["transcript"], "the serial console still needs to show what was heard"
    assert client.get("/api/v1/devices/living_room_light").json()["state"]["power"] == before


def test_upload_audio_answers_a_bare_summons(client: TestClient) -> None:
    res = client.post("/upload-audio", content="Anh Thạc Sĩ ơi".encode() + b" " * 120)

    data = res.json()
    assert data["success"] is True
    assert "Anh Thạc Sĩ" in data["response"]
    assert data["error"] is None


def test_upload_audio_follow_up_needs_no_name(client: TestClient) -> None:
    """One session id for the board, so the window opened by the summon holds."""
    client.post("/upload-audio", content="Anh Thạc Sĩ, bật đèn phòng khách".encode() + b" " * 120)

    res = client.post("/upload-audio", content="tắt đèn phòng khách".encode() + b" " * 120)
    data = res.json()

    assert data["success"] is True
    assert data["states"]["lr_main"] == 0

