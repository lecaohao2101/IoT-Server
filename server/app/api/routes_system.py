"""Liveness, readiness, introspection and the live log tail."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Response, status
from fastapi.responses import HTMLResponse

from app import __version__
from app.api.deps import Container, CurrentPrincipal
from app.core.errors import UnauthorizedError
from app.core.log_stream import log_tail
from app.core.security import authenticate, extract_bearer
from app.web.log_viewer import LOG_VIEWER_HTML

router = APIRouter(tags=["system"])


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, str]:
    """Answers as long as the process can serve requests. Never touches a dependency."""
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe")
async def readyz(
    container: Container,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Reports the state store and the MQTT link; 503 when either is unusable.

    Detail is for operators. An unauthenticated caller gets the verdict and the
    status code it needs for a health check, without a description of the
    infrastructure, the providers in use and the size of the apartment.
    """
    health = await container.health()
    if health["status"] != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    if not container.settings.auth_enabled:
        return health
    try:
        authenticate(
            container.settings, token=extract_bearer(authorization) or x_api_key
        )
    except UnauthorizedError:
        # The commit of a public repository is not a secret, and a deployment
        # pipeline needs to confirm which build is serving without holding a
        # credential.
        return {
            "status": health["status"],
            "version": __version__,
            "build": container.settings.build_sha,
        }
    return health


@router.get("/api/v1/system/info", summary="Server and apartment summary")
async def system_info(container: Container, _: CurrentPrincipal) -> dict[str, Any]:
    settings = container.settings
    return {
        "app": settings.app_name,
        "version": __version__,
        "build": settings.build_sha,
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
        "anonymous_allowed": not settings.auth_enabled,
    }


@router.get("/api/v1/logs", summary="Recent log records with their structured fields")
async def recent_logs(
    _: CurrentPrincipal,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> dict[str, Any]:
    """Everything logged since sequence ``after``.

    Credentialed: these records carry transcripts and internal detail, which is
    why this sits behind the same key as the rest of the API.
    """
    records, newest = log_tail.since(after=after, limit=limit)
    return {"records": records, "last_seq": newest}


@router.get("/logs", summary="Live log viewer", response_class=HTMLResponse)
async def log_viewer_page() -> HTMLResponse:
    """The page itself carries no data -- it asks for a key and then polls."""
    return HTMLResponse(content=LOG_VIEWER_HTML)
