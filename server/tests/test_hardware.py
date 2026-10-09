"""Tests for ESP32 hardware compatibility routes."""

from __future__ import annotations

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
            "lr_angle": 30,
            "bed_angle": 0,
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
    assert "living_room_ac" in data["updated"]
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
    assert data["states"]["lr_angle"] == 30
    assert data["states"]["bed_angle"] == 0

    # Verify returned room-based structure
    assert "rooms" in data
    assert data["rooms"]["living_room"]["main_light"] == 1
    assert data["rooms"]["living_room"]["sofa_reading_light"] == 1
    assert data["rooms"]["kitchen"]["main_light"] == 0
    assert data["rooms"]["bedroom"]["main_light"] == 1

    # Verify device state endpoints reflect the hardware report
    lr_light = client.get("/api/v1/devices/living_room_light").json()
    assert lr_light["state"]["power"] == "on"

    lr_ac = client.get("/api/v1/devices/living_room_ac").json()
    assert lr_ac["state"]["power"] == "on"
    assert lr_ac["state"]["vane_angle"] == 30

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
    assert "lr_angle" in data["states"]


def test_upload_audio_short_payload_rejected(client: TestClient) -> None:
    res = client.post("/upload-audio", content=b"short")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is False
    assert data["error"] == "empty_audio"


def test_upload_audio_executes_voice_command(client: TestClient) -> None:
    # Under mock STT, valid UTF-8 prose becomes the recognized transcript
    audio_text = "bật đèn phòng khách".encode()
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
