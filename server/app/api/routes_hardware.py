"""Hardware compatibility router for the ESP32 smart home microcontroller.

Bridges communication with the EnvMonitor-SmarHome firmware:
- POST /update-status: Ingests button triggers, LED status, servo angles, and sensor readings.
- POST /upload-audio: Receives 16kHz PCM audio buffers from the INMP441 microphone and executes commands.
- GET /poll-commands: Returns current authoritative device and pin states.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from app.ai.base import AudioEncoding
from app.api.deps import Container

log = logging.getLogger(__name__)

router = APIRouter(tags=["hardware"])


class HardwareStatusPayload(BaseModel):
    buttons: dict[str, bool] = Field(default_factory=dict)
    leds: dict[str, bool] = Field(default_factory=dict)
    servos: dict[str, int | float] = Field(default_factory=dict)
    sensors: dict[str, float | int] = Field(default_factory=dict)


LED_DEVICE_MAP: dict[str, str] = {
    "lr_main": "living_room_light",
    "lr_sofa": "living_room_sofa_light",
    "kit_main": "kitchen_light",
    "bed_main": "bedroom_light",
    "bed_side": "bedroom_side_light",
    "study": "study_light",
    "balcony": "balcony_light",
    "wc": "bathroom_light",
}


async def _build_pin_states(container: Container) -> dict[str, int]:
    """Helper to read current authoritative states and format as hardware pin states."""
    snapshot = await container.state.snapshot()

    def _is_on(device_id: str) -> int:
        return 1 if snapshot.value(device_id, "power") == "on" else 0

    lr_angle = snapshot.value("living_room_ac", "vane_angle", 0)
    if snapshot.value("living_room_ac", "power") != "on":
        lr_angle = 0

    bed_angle = snapshot.value("bedroom_ac", "vane_angle", 0)
    if snapshot.value("bedroom_ac", "power") != "on":
        bed_angle = 0

    return {
        "lr_main": _is_on("living_room_light"),
        "lr_sofa": _is_on("living_room_sofa_light"),
        "kit_main": _is_on("kitchen_light"),
        "bed_main": _is_on("bedroom_light"),
        "bed_side": _is_on("bedroom_side_light"),
        "study": _is_on("study_light"),
        "balcony": _is_on("balcony_light"),
        "wc": _is_on("bathroom_light"),
        "lr_angle": int(lr_angle),
        "bed_angle": int(bed_angle),
    }


async def _build_room_states(container: Container) -> dict[str, dict[str, int]]:
    """Helper to group device power states by room, matching high-level dashboard views."""
    snapshot = await container.state.snapshot()

    def _is_on(device_id: str) -> int:
        return 1 if snapshot.value(device_id, "power") == "on" else 0

    return {
        "living_room": {
            "main_light": _is_on("living_room_light"),
            "sofa_reading_light": _is_on("living_room_sofa_light"),
            "air_conditioner_1": _is_on("living_room_ac"),
            "curtain": 1 if snapshot.value("living_room_curtain", "state") == "open" else 0,
        },
        "kitchen": {
            "main_light": _is_on("kitchen_light"),
        },
        "bedroom": {
            "main_light": _is_on("bedroom_light"),
            "bedside_reading_light": _is_on("bedroom_side_light"),
            "air_conditioner_2": _is_on("bedroom_ac"),
            "curtain": 1 if snapshot.value("bedroom_curtain", "state") == "open" else 0,
        },
        "study": {
            "light": _is_on("study_light"),
        },
        "balcony": {
            "light": _is_on("balcony_light"),
        },
        "bathroom": {
            "light": _is_on("bathroom_light"),
        },
    }


@router.post(
    "/update-status",
    summary="Update hardware status from ESP32",
    status_code=status.HTTP_200_OK,
)
@router.post(
    "/api/v1/hardware/update-status",
    summary="Update hardware status from ESP32 (API v1 alias)",
    status_code=status.HTTP_200_OK,
)
async def update_status(payload: HardwareStatusPayload, container: Container) -> dict[str, Any]:
    updated_devices: list[str] = []

    # 1. Update LEDs
    for led_key, state in payload.leds.items():
        device_id = LED_DEVICE_MAP.get(led_key)
        if device_id and device_id in container.home.device_map:
            power_val = "on" if state else "off"
            await container.state.apply_reported(device_id, {"power": power_val})
            await container.state.set_availability(device_id, True)
            updated_devices.append(device_id)

    # 2. Update Servos (Living room & Bedroom AC vanes)
    if "lr_angle" in payload.servos:
        angle = int(payload.servos["lr_angle"])
        power_val = "on" if angle > 0 else "off"
        await container.state.apply_reported(
            "living_room_ac", {"vane_angle": angle, "power": power_val}
        )
        await container.state.set_availability("living_room_ac", True)
        updated_devices.append("living_room_ac")

    if "bed_angle" in payload.servos:
        angle = int(payload.servos["bed_angle"])
        power_val = "on" if angle > 0 else "off"
        await container.state.apply_reported(
            "bedroom_ac", {"vane_angle": angle, "power": power_val}
        )
        await container.state.set_availability("bedroom_ac", True)
        updated_devices.append("bedroom_ac")

    # 3. Update Sensors
    sensor_living: dict[str, Any] = {}
    if "temp" in payload.sensors:
        sensor_living["temperature"] = float(payload.sensors["temp"])
    if "hum" in payload.sensors:
        sensor_living["humidity"] = float(payload.sensors["hum"])
    if "sound_level" in payload.sensors:
        sensor_living["sound_level"] = int(payload.sensors["sound_level"])

    if sensor_living and "living_room_sensor" in container.home.device_map:
        await container.state.apply_reported("living_room_sensor", sensor_living)
        await container.state.set_availability("living_room_sensor", True)
        updated_devices.append("living_room_sensor")

    if "distance_cm" in payload.sensors and "bedroom_sensor" in container.home.device_map:
        await container.state.apply_reported(
            "bedroom_sensor", {"distance_cm": float(payload.sensors["distance_cm"])}
        )
        await container.state.set_availability("bedroom_sensor", True)
        updated_devices.append("bedroom_sensor")

    states = await _build_pin_states(container)
    rooms = await _build_room_states(container)
    return {
        "success": True,
        "updated": updated_devices,
        "states": states,
        "rooms": rooms,
    }


@router.post(
    "/upload-audio",
    summary="Upload 16kHz PCM audio from ESP32 mic",
    status_code=status.HTTP_200_OK,
)
@router.post(
    "/api/v1/hardware/upload-audio",
    summary="Upload 16kHz PCM audio from ESP32 mic (API v1 alias)",
    status_code=status.HTTP_200_OK,
)
async def upload_audio(request: Request, container: Container) -> dict[str, Any]:
    body = await request.body()
    if len(body) < 100:
        return {
            "success": False,
            "error": "empty_audio",
            "message": "Audio payload too short or empty",
        }

    # Transcribe audio using STT
    stream = container.stt.open_stream(language="vi-VN", sample_rate=16000)
    user_text = ""
    try:
        await stream.push(body)
        await stream.end_of_audio()
        async for transcript in stream.results():
            if transcript.is_final and transcript.text.strip():
                user_text = transcript.text.strip()
    finally:
        await stream.aclose()

    if not user_text:
        return {
            "success": False,
            "transcript": "",
            "response": "Không phát hiện giọng nói rõ ràng.",
            "error": "stt_empty",
        }

    # Execute intent through the conversation orchestrator
    turn = await container.orchestrator.run_turn(
        session_id="esp32_hardware",
        user_text=user_text,
        speak=True,
        encoding=AudioEncoding.PCM16,
    )

    states = await _build_pin_states(container)
    rooms = await _build_room_states(container)
    return {
        "success": True,
        "transcript": user_text,
        "response": turn.speech,
        "plan": turn.plan.as_dict() if turn.plan else None,
        "dispatched": turn.report.delivered_count if turn.report else 0,
        "states": states,
        "rooms": rooms,
    }


@router.get(
    "/poll-commands",
    summary="Poll current pin states for HTTP sync",
    status_code=status.HTTP_200_OK,
)
@router.get(
    "/api/v1/hardware/poll-commands",
    summary="Poll current pin states for HTTP sync (API v1 alias)",
    status_code=status.HTTP_200_OK,
)
async def poll_commands(container: Container) -> dict[str, Any]:
    states = await _build_pin_states(container)
    rooms = await _build_room_states(container)
    return {
        "success": True,
        "states": states,
        "rooms": rooms,
    }
