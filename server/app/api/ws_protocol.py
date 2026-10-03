"""The WebSocket wire contract, shared by the ESP32 firmware and the mobile app.

Text frames carry JSON control messages; **binary frames carry audio** and nothing
else. In the client -> server direction binary frames are microphone PCM; in the
server -> client direction they are synthesised speech, always preceded by a
``tts.start`` describing the encoding and followed by ``tts.end``.

Audio format, both directions: 16 kHz, mono, signed 16-bit little-endian PCM,
headerless. A client that records at another rate declares it in ``hello`` and the
server resamples.

    client ──► {"type":"hello","room":"living_room","sample_rate":16000}
    server ──► {"type":"session.ready","session_id":"..."}
    client ──► <binary PCM frames...>
    client ──► {"type":"audio.end"}
    server ──► {"type":"stt.partial","text":"bật đèn"}
    server ──► {"type":"stt.final","text":"bật đèn phòng khách"}
    server ──► {"type":"assistant.delta","text":"Đã bật"}
    server ──► {"type":"tts.start","encoding":"pcm16","sample_rate":16000}
    server ──► <binary PCM frames...>
    server ──► {"type":"tts.end"}
    server ──► {"type":"assistant.final","text":"...","plan":{...}}
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.utils import utc_iso

# --------------------------------------------------------------- client -> server


class HelloMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["hello"]
    room: str | None = None
    device_id: str | None = None
    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    channels: int = Field(default=1, ge=1, le=2)
    #: ``pcm16`` is the only inbound codec; declared explicitly so a future
    #: opus-capable firmware fails loudly rather than streaming noise.
    codec: Literal["pcm16"] = "pcm16"
    #: What the client wants back. A phone on mobile data should ask for mp3.
    reply_encoding: Literal["pcm16", "mp3", "wav", "none"] = "pcm16"
    session_id: str | None = None


class AudioStartMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["audio.start"]


class AudioEndMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["audio.end"]


class TextMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["text"]
    text: str = Field(min_length=1, max_length=2000)


class CancelMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["cancel"]


class PingMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["ping"]


ClientMessage = (
    HelloMessage | AudioStartMessage | AudioEndMessage | TextMessage | CancelMessage | PingMessage
)

_CLIENT_TYPES: dict[str, type[BaseModel]] = {
    "hello": HelloMessage,
    "audio.start": AudioStartMessage,
    "audio.end": AudioEndMessage,
    "text": TextMessage,
    "cancel": CancelMessage,
    "ping": PingMessage,
}


def parse_client_message(payload: dict[str, Any]) -> BaseModel:
    """Validate an inbound control frame. Raises ``ValueError`` on anything unknown."""
    kind = payload.get("type")
    model = _CLIENT_TYPES.get(kind) if isinstance(kind, str) else None
    if model is None:
        raise ValueError(f"unsupported message type: {kind!r}")
    return model.model_validate(payload)


# --------------------------------------------------------------- server -> client


def _frame(kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "ts": utc_iso(), **fields}


def session_ready(session_id: str, *, room: str | None, sample_rate: int, **extra: Any):
    return _frame("session.ready", session_id=session_id, room=room,
                  sample_rate=sample_rate, **extra)


def stt_partial(text: str):
    return _frame("stt.partial", text=text)


def stt_final(text: str, confidence: float = 0.0):
    return _frame("stt.final", text=text, confidence=round(confidence, 3))


def assistant_delta(text: str):
    return _frame("assistant.delta", text=text)


def assistant_final(text: str, plan: dict[str, Any] | None = None, **extra: Any):
    return _frame("assistant.final", text=text, plan=plan, **extra)


def tts_start(encoding: str, sample_rate: int):
    return _frame("tts.start", encoding=encoding, sample_rate=sample_rate)


def tts_end():
    return _frame("tts.end")


def state_changed(device_id: str, state: dict[str, Any], online: bool):
    return _frame("state.changed", device_id=device_id, state=state, online=online)


def notice(code: str, message: str):
    return _frame("notice", code=code, message=message)


def error(code: str, message: str, **details: Any):
    return _frame("error", code=code, message=message, **details)


def pong():
    return _frame("pong")


def cancelled():
    return _frame("cancelled")
