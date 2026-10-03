"""Liveness, readiness and introspection endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status

from app.api.deps import Container

router = APIRouter(tags=["system"])


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, str]:
    """Answers as long as the process can serve requests. Never touches a dependency."""
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe")
async def readyz(container: Container, response: Response) -> dict[str, Any]:
    """Reports the state store and the MQTT link; 503 when either is unusable."""
    health = await container.health()
    if health["status"] != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return health


@router.get("/api/v1/system/info", summary="Server and apartment summary")
async def system_info(container: Container) -> dict[str, Any]:
    settings = container.settings
    return {
        "app": settings.app_name,
        "env": settings.app_env,
        "uptime_s": round(container.uptime_s, 1),
        "home": {
            "name": container.home.name,
            "timezone": container.home.timezone,
            "rooms": [{"id": r.id, "name": r.name} for r in container.home.rooms],
            "device_count": len(container.home.devices),
            "scene_count": len(container.home.scenes),
        },
        "providers": {
            "stt": container.stt.name,
            "tts": container.tts.name,
            "llm": container.llm.name,
        },
        "audio": {
            "sample_rate": settings.audio_sample_rate,
            "channels": settings.audio_channels,
            "encoding": "pcm16",
            "silence_timeout_ms": settings.silence_timeout_ms,
            "max_utterance_s": settings.max_utterance_s,
        },
        "safety": {
            "max_commands_per_plan": container.policy.max_commands_per_plan,
            "quiet_hours_enabled": container.policy.quiet_hours.enabled,
            "quiet_hours_now": container.validator.in_quiet_hours(),
        },
        "auth_required": settings.auth_enabled,
    }
