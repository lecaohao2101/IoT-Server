"""Text conversation and one-shot synthesis.

The same orchestrator the voice socket uses, with speech output disabled. Useful
for the app's text box, for scripted tests, and for debugging a turn without a
microphone in the loop.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.ai.base import AudioEncoding
from app.api.deps import Container, CurrentPrincipal
from app.api.schemas import (
    ChatHistoryResponse,
    ChatRequest,
    ChatResponse,
    SpeakRequest,
    TranscriptTurn,
)
from app.core.audio import AudioFormat, pcm_to_wav
from app.core.utils import new_id

router = APIRouter(prefix="/api/v1", tags=["conversation"])


@router.post("/chat", response_model=ChatResponse, summary="Send a text turn")
async def chat(body: ChatRequest, container: Container, principal: CurrentPrincipal) -> ChatResponse:
    session_id = body.session_id or new_id("chat")
    result = await container.orchestrator.run_turn(
        session_id=session_id,
        user_text=body.text,
        room=body.room,
        speak=False,
    )
    return ChatResponse(
        session_id=session_id,
        speech=result.speech,
        plan=result.plan.as_dict() if result.plan else None,
        dispatched=result.report.delivered_count if result.report else 0,
        needs_clarification=result.needs_clarification,
        latency_ms={k: round(v, 1) for k, v in result.latency_ms.items()},
        error=result.error,
    )


@router.get(
    "/chat/{session_id}",
    response_model=ChatHistoryResponse,
    summary="Read a session transcript",
)
async def get_history(
    session_id: str, container: Container, _: CurrentPrincipal
) -> ChatHistoryResponse:
    ctx = await container.conversations.get(session_id)
    return ChatHistoryResponse(
        session_id=session_id,
        room=ctx.room,
        turns=[
            TranscriptTurn(role=t.role.value, text=t.text, ts=t.ts, commands=t.commands)
            for t in ctx.turns
        ],
        pending=ctx.pending.model_dump() if ctx.pending else None,
    )


@router.delete(
    "/chat/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Forget a session",
)
async def clear_history(session_id: str, container: Container, _: CurrentPrincipal) -> Response:
    await container.conversations.clear(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/speak",
    summary="Synthesise speech",
    response_class=Response,
    responses={200: {"content": {"audio/wav": {}, "audio/mpeg": {}}}},
)
async def speak(body: SpeakRequest, container: Container, _: CurrentPrincipal) -> Response:
    """Return playable audio for arbitrary text -- announcements, doorbell messages."""
    settings = container.settings
    audio = await container.tts.synthesize(
        body.text,
        encoding=AudioEncoding.PCM16,
        sample_rate=settings.audio_sample_rate,
        voice=body.voice,
    )
    if audio.encoding is AudioEncoding.MP3:
        return Response(content=audio.audio, media_type="audio/mpeg")
    payload = pcm_to_wav(audio.audio, AudioFormat(sample_rate=audio.sample_rate))
    return Response(content=payload, media_type="audio/wav")
