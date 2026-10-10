"""One live WebSocket conversation.

The session owns an utterance state machine. Audio frames arriving while the
assistant is idle open a recognition stream; the stream closes when the client
says ``audio.end``, when the recogniser reports end-of-speech, or when our own
energy endpointer decides the user has stopped. The resulting transcript starts a
turn, and the turn's output is streamed straight back down the same socket.

Barge-in is the reason the turn runs as a separate task: a frame of microphone
audio arriving while the assistant is speaking cancels the turn immediately, and
the socket is free to start the next utterance without waiting for TTS to drain.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from app.ai.base import AudioEncoding, SpeechRecognizer, SttStream
from app.api import ws_protocol as proto
from app.config import Settings
from app.core.audio import AudioFormat, SilenceEndpointer, resample, to_mono
from app.core.errors import AppError
from app.core.eventbus import TOPIC_DEVICE_AVAILABILITY, TOPIC_DEVICE_STATE, EventBus
from app.core.security import Principal
from app.core.utils import new_id, truncate
from app.logging_setup import trace_id_var
from app.services.orchestrator import Orchestrator, TurnSink

log = logging.getLogger(__name__)

_TARGET_RATE_KEY = "sample_rate"


class VoiceSession(TurnSink):
    def __init__(
        self,
        websocket: Any,
        *,
        orchestrator: Orchestrator,
        recognizer: SpeechRecognizer,
        settings: Settings,
        bus: EventBus,
        principal: Principal,
        session_id: str | None = None,
        room: str | None = None,
        audio_hub: Any = None,
    ) -> None:
        self._ws = websocket
        self._orchestrator = orchestrator
        self._recognizer = recognizer
        self._s = settings
        self._bus = bus
        self._principal = principal
        self._audio_hub = audio_hub

        self.session_id = session_id or new_id("sess")
        self.room = room
        self._client_rate = settings.audio_sample_rate
        self._client_channels = 1
        self._reply_encoding = AudioEncoding.PCM16
        self._client_wants_audio = True

        self._fmt = AudioFormat(sample_rate=settings.audio_sample_rate)
        self._endpointer = SilenceEndpointer(
            self._fmt, silence_ms=settings.silence_timeout_ms
        )

        self._stream: SttStream | None = None
        self._stt_task: asyncio.Task[None] | None = None
        self._turn_task: asyncio.Task[None] | None = None
        self._state_task: asyncio.Task[None] | None = None

        self._utterance_bytes = 0
        self._utterance_started = 0.0
        self._audio_ended = False
        self._cancelled = False
        self._closed = False
        self._greeted = False

    # ------------------------------------------------------------- TurnSink
    @property
    def cancelled(self) -> bool:
        return self._cancelled or self._closed

    @property
    def _speak(self) -> bool:
        """Whether this turn is worth synthesising at all.

        The board holding the microphone asks for ``reply_encoding: "none"``
        because its reply comes out of a Bluetooth speaker wired to a *different*
        board, which pulls from the audio hub. Tying synthesis to this socket's
        encoding would leave that speaker silent for every spoken command.
        """
        if self._client_wants_audio:
            return True
        return bool(self._audio_hub and self._audio_hub.active_subscribers_count > 0)

    async def assistant_delta(self, text: str) -> None:
        await self._send(proto.assistant_delta(text))

    async def audio_start(self, encoding: AudioEncoding, sample_rate: int) -> None:
        if self._client_wants_audio:
            await self._send(proto.tts_start(encoding.value, sample_rate))

    async def audio_chunk(self, payload: bytes) -> None:
        if not payload or self.cancelled:
            return
        if self._client_wants_audio:
            await self._send_bytes(payload)
        if self._audio_hub:
            await self._audio_hub.broadcast_chunk(payload, src_rate=self._fmt.sample_rate)

    async def audio_end(self) -> None:
        if self._client_wants_audio:
            await self._send(proto.tts_end())

    async def notice(self, code: str, message: str) -> None:
        await self._send(proto.notice(code, message))

    # ----------------------------------------------------------------- loop
    async def run(self) -> None:
        trace_id_var.set(self.session_id)
        log.info(
            "voice session opened",
            extra={"session_id": self.session_id, "principal": str(self._principal)},
        )
        self._state_task = asyncio.create_task(self._forward_state_events(), name="ws-state")
        try:
            while not self._closed:
                try:
                    message = await asyncio.wait_for(
                        self._ws.receive(), timeout=self._s.ws_recv_timeout_s
                    )
                except TimeoutError:
                    await self._send(proto.error("idle_timeout", "Phiên không hoạt động."))
                    break

                kind = message.get("type")
                if kind == "websocket.disconnect":
                    break
                if (payload := message.get("bytes")) is not None:
                    await self._on_audio(payload)
                elif (text := message.get("text")) is not None:
                    await self._on_text(text)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the socket is going away either way
            log.exception("voice session failed", extra={"session_id": self.session_id})
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._abort_turn()
        await self._end_recognition(discard=True)
        if self._state_task:
            self._state_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._state_task
        log.info("voice session closed", extra={"session_id": self.session_id})

    # ------------------------------------------------------------- control
    async def _on_text(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("control frame must be a JSON object")
            message = proto.parse_client_message(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            await self._send(proto.error("bad_message", str(exc)))
            return

        kind = payload.get("type")
        if kind == "hello":
            await self._on_hello(message)
        elif kind == "audio.start":
            await self._reset_utterance()
        elif kind == "audio.end":
            await self._end_recognition()
        elif kind == "text":
            await self._cancel_active("user_text")
            await self._start_turn(message.text)  # type: ignore[attr-defined]
        elif kind == "cancel":
            await self._cancel_active("user_cancel")
            await self._send(proto.cancelled())
        elif kind == "ping":
            await self._send(proto.pong())

    async def _on_hello(self, message: Any) -> None:
        if message.session_id:
            self.session_id = message.session_id
            trace_id_var.set(self.session_id)
        self.room = message.room or self.room
        self._client_rate = message.sample_rate
        self._client_channels = message.channels
        self._client_wants_audio = message.reply_encoding != "none"
        if message.reply_encoding in {"pcm16", "mp3", "wav"}:
            self._reply_encoding = AudioEncoding(message.reply_encoding)
        await self._greet()

    async def _greet(self) -> None:
        if self._greeted:
            return
        self._greeted = True
        await self._send(
            proto.session_ready(
                self.session_id,
                room=self.room,
                sample_rate=self._s.audio_sample_rate,
                reply_encoding=self._reply_encoding.value if self._client_wants_audio else "none",
                silence_timeout_ms=self._s.silence_timeout_ms,
                max_utterance_s=self._s.max_utterance_s,
            )
        )

    # --------------------------------------------------------------- audio
    async def _on_audio(self, payload: bytes) -> None:
        if not payload:
            return
        await self._greet()

        # A frame while the assistant is talking means the user cut in.
        if self._turn_task is not None and not self._turn_task.done():
            await self._cancel_active("barge_in")

        frame = self._normalise(payload)
        if self._stream is None:
            await self._reset_utterance()

        self._utterance_bytes += len(frame)
        if self._utterance_bytes > self._s.max_audio_bytes_per_utterance:
            await self._send(proto.error("utterance_too_long", "Câu nói quá dài."))
            await self._end_recognition()
            return

        assert self._stream is not None
        await self._stream.push(frame)

        if self._endpointer.accept(frame):
            await self._end_recognition()
            return
        if (time.monotonic() - self._utterance_started) > self._s.max_utterance_s:
            await self._end_recognition()

    def _normalise(self, payload: bytes) -> bytes:
        """Bring the client's audio to the server's canonical 16 kHz mono PCM16."""
        frame = payload
        if self._client_channels > 1:
            frame = to_mono(frame, self._client_channels)
        if self._client_rate != self._s.audio_sample_rate:
            frame = resample(frame, self._client_rate, self._s.audio_sample_rate)
        return frame

    async def _reset_utterance(self) -> None:
        await self._end_recognition(discard=True)
        self._endpointer.reset()
        self._utterance_bytes = 0
        self._utterance_started = time.monotonic()
        self._audio_ended = False
        self._cancelled = False
        self._stream = self._recognizer.open_stream(
            language=self._s.stt_language, sample_rate=self._s.audio_sample_rate
        )
        self._stt_task = asyncio.create_task(self._consume_transcripts(), name="stt-consumer")
        log.info(
            "stt utterance opened",
            extra={
                "session_id": self.session_id,
                "provider": self._recognizer.name,
                "language": self._s.stt_language,
                "sample_rate": self._s.audio_sample_rate,
                "client_rate": self._client_rate,
                "client_channels": self._client_channels,
            },
        )

    async def _end_recognition(self, discard: bool = False) -> None:
        stream, task = self._stream, self._stt_task
        self._audio_ended = True
        if stream is None:
            return
        if discard:
            self._stream, self._stt_task = None, None
            await stream.aclose()
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            return
        log.info(
            "stt audio received",
            extra={
                "session_id": self.session_id,
                "bytes": self._utterance_bytes,
                "duration_ms": round(
                    self._utterance_bytes * 1000.0 / self._fmt.bytes_per_second, 1
                ),
                "peak_level": self._endpointer.peak_level,
                "noise_floor": self._endpointer.noise_floor,
                "speech_ms": round(self._endpointer.speech_ms, 1),
            },
        )
        await stream.end_of_audio()

    async def _consume_transcripts(self) -> None:
        stream = self._stream
        if stream is None:
            return
        final_text = ""
        confidence = 0.0
        started = time.monotonic()
        heard_partial = False
        try:
            async for result in stream.results():
                if self._closed:
                    break
                if result.is_final and result.text:
                    final_text, confidence = result.text, result.confidence
                    break
                if result.text:
                    if not heard_partial:
                        # The first partial is the proof that audio is both
                        # arriving and recognisable -- worth one line; the rest
                        # arrive several times a second and stay at debug.
                        heard_partial = True
                        log.info(
                            "stt first partial",
                            extra={
                                "session_id": self.session_id,
                                "after_ms": round((time.monotonic() - started) * 1000, 1),
                                **self._text_field(result.text),
                            },
                        )
                    else:
                        log.debug(
                            "stt partial",
                            extra={"session_id": self.session_id, **self._text_field(result.text)},
                        )
                    await self._send(proto.stt_partial(result.text))
                if result.speech_ended and not self._audio_ended:
                    await self._end_recognition()
        except asyncio.CancelledError:
            raise
        except AppError as exc:
            await self._send(proto.error(exc.code, exc.message))
        except Exception as exc:  # noqa: BLE001
            log.exception("recognition failed", extra={"session_id": self.session_id})
            await self._send(proto.error("stt_error", str(exc)))
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()
            if self._stream is stream:
                self._stream = None
                self._stt_task = None

        log.info(
            "stt transcript" if final_text else "stt produced nothing",
            extra={
                "session_id": self.session_id,
                "confidence": round(confidence, 3),
                "after_ms": round((time.monotonic() - started) * 1000, 1),
                **self._text_field(final_text),
            },
        )

        if final_text and not self._closed:
            await self._send(proto.stt_final(final_text, confidence))
            await self._start_turn(final_text)

    # ---------------------------------------------------------------- turn
    async def _start_turn(self, text: str) -> None:
        await self._abort_turn()
        self._cancelled = False
        self._turn_task = asyncio.create_task(self._run_turn(text), name="turn")

    async def _run_turn(self, text: str) -> None:
        speak = self._speak
        if not speak:
            # The mic board asks for no audio and nothing is pulling from the hub,
            # so this reply will be silent. Say so: a speaker that dropped off is
            # otherwise indistinguishable from TTS being broken.
            log.warning(
                "tts skipped -- nobody is listening",
                extra={
                    "session_id": self.session_id,
                    "client_wants_audio": self._client_wants_audio,
                    "hub_subscribers": (
                        self._audio_hub.active_subscribers_count if self._audio_hub else 0
                    ),
                },
            )
        try:
            result = await self._orchestrator.run_turn(
                session_id=self.session_id,
                user_text=text,
                room=self.room,
                sink=self,
                speak=speak,
                encoding=self._reply_encoding,
                sample_rate=self._s.audio_sample_rate,
            )
        except asyncio.CancelledError:
            raise
        except AppError as exc:
            await self._send(proto.error(exc.code, exc.message))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("turn failed", extra={"session_id": self.session_id})
            await self._send(proto.error("turn_failed", str(exc)))
            return

        if self.cancelled:
            return
        await self._send(
            proto.assistant_final(
                result.speech,
                plan=result.plan.as_dict() if result.plan else None,
                needs_clarification=result.needs_clarification,
                latency_ms={k: round(v) for k, v in result.latency_ms.items()},
            )
        )

    async def _cancel_active(self, reason: str) -> None:
        if self._turn_task is None or self._turn_task.done():
            return
        self._cancelled = True
        log.info("turn interrupted", extra={"session_id": self.session_id, "reason": reason})
        await self._abort_turn()

    async def _abort_turn(self) -> None:
        task, self._turn_task = self._turn_task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    # ------------------------------------------------------------- outbound
    async def _forward_state_events(self) -> None:
        """Push device state changes down the same socket the dashboard listens on."""
        try:
            async for event in self._bus.subscribe(TOPIC_DEVICE_STATE, TOPIC_DEVICE_AVAILABILITY):
                if self._closed:
                    return
                payload = event.payload
                await self._send(
                    proto.state_changed(
                        payload.get("device_id", ""),
                        payload.get("state", {}),
                        bool(payload.get("online", False)),
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.debug("state forwarding stopped", exc_info=True)

    def _text_field(self, text: str) -> dict[str, Any]:
        """What to log of recognised or spoken text, honouring ``log_transcripts``."""
        if self._s.log_transcripts:
            return {"text": truncate(text, 200), "chars": len(text)}
        return {"chars": len(text)}

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._closed:
            return
        try:
            await self._ws.send_json(payload)
        except Exception:  # noqa: BLE001 - client vanished mid-reply
            self._closed = True

    async def _send_bytes(self, payload: bytes) -> None:
        if self._closed:
            return
        try:
            await self._ws.send_bytes(payload)
        except Exception:  # noqa: BLE001
            self._closed = True
