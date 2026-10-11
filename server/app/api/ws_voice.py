"""WebSocket endpoints.

``/ws/voice``  full-duplex audio: the ESP32 and the mobile app both land here.
``/ws/events`` read-only state feed for a dashboard that is not holding a mic open.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.api import ws_protocol as proto
from app.api.deps import WsContainer, authenticate_websocket, get_ws_container
from app.core.errors import UnauthorizedError
from app.core.eventbus import (
    TOPIC_COMMAND_DISPATCHED,
    TOPIC_CONVERSATION_TURN,
    TOPIC_DEVICE_AVAILABILITY,
    TOPIC_DEVICE_STATE,
)
from app.services.session import VoiceSession

log = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

_active_sessions = 0


def _offered_credential(websocket: WebSocket, token: str | None) -> str:
    """Where a credential came from, never the credential itself.

    "none" and "query" are different bugs: the first is firmware that forgot the
    token, the second is a token that simply does not match.
    """
    if token:
        return "query"
    if websocket.headers.get("authorization"):
        return "header:authorization"
    if websocket.headers.get("x-api-key"):
        return "header:x-api-key"
    return "none"


@router.websocket("/ws/voice")
async def voice_endpoint(
    websocket: WebSocket,
    container: WsContainer,
    token: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    room: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
) -> None:
    global _active_sessions

    try:
        principal = authenticate_websocket(container, websocket, token, device_id)
    except UnauthorizedError as exc:
        # A socket turned away here never reaches accept(), so it produces no
        # access line either: without this the device simply vanishes from the
        # logs and the symptom reads as "the firmware never called".
        log.warning(
            "voice socket rejected",
            extra={
                "reason": exc.code,
                "device_id": device_id,
                "room": room,
                "credential_offered": _offered_credential(websocket, token),
                "client": websocket.client.host if websocket.client else None,
            },
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=exc.message)
        return

    if _active_sessions >= container.settings.ws_max_sessions:
        log.warning(
            "voice socket refused -- session limit reached",
            extra={"active": _active_sessions, "limit": container.settings.ws_max_sessions},
        )
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER, reason="Too many sessions")
        return

    await websocket.accept()
    _active_sessions += 1
    session = VoiceSession(
        websocket,
        orchestrator=container.orchestrator,
        recognizer=container.stt,
        settings=container.settings,
        bus=container.bus,
        principal=principal,
        session_id=session_id,
        room=room,
        audio_hub=container.audio_hub,
        speaker_manager=container.speaker_manager,
    )
    try:
        await session.run()
    except WebSocketDisconnect:
        pass
    finally:
        _active_sessions -= 1
        await session.close()
        with contextlib.suppress(Exception):
            await websocket.close()


@router.websocket("/ws/events")
async def events_endpoint(
    websocket: WebSocket,
    token: str | None = Query(default=None),
) -> None:
    """Live feed of device state, dispatched commands and completed turns."""
    container = get_ws_container(websocket)
    try:
        authenticate_websocket(container, websocket, token, None)
    except UnauthorizedError as exc:
        log.warning(
            "events socket rejected",
            extra={
                "reason": exc.code,
                "credential_offered": _offered_credential(websocket, token),
                "client": websocket.client.host if websocket.client else None,
            },
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=exc.message)
        return

    await websocket.accept()
    await websocket.send_json(
        proto.session_ready("events", room=None, sample_rate=container.settings.audio_sample_rate)
    )

    reader = asyncio.create_task(_drain(websocket), name="events-reader")
    try:
        async for event in container.bus.subscribe(
            TOPIC_DEVICE_STATE,
            TOPIC_DEVICE_AVAILABILITY,
            TOPIC_COMMAND_DISPATCHED,
            TOPIC_CONVERSATION_TURN,
        ):
            if reader.done():
                break
            await websocket.send_json(event.as_dict())
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.debug("events socket closed", exc_info=True)
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader
        with contextlib.suppress(Exception):
            await websocket.close()


async def _drain(websocket: WebSocket) -> None:
    """Consume (and ignore) client frames so a disconnect is noticed promptly."""
    with contextlib.suppress(Exception):
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
