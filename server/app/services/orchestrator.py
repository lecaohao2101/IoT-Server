"""The conversation orchestrator: one user utterance in, one apartment reaction out.

Pipeline for a single turn::

    text ──► confirmation check ──► prompt build ──► LLM (streamed JSON)
                                                      │
                     speech field ──► clause chunker ─┴─► TTS ──► audio frames
                                                      │
                     commands ──► safety validator ──► MQTT dispatch ──► state

Two properties matter more than anything else here:

**Latency.** The ``speech`` field is pulled out of the JSON while it is still being
generated and handed to a synthesis worker that runs concurrently with the rest of
the model's output. The user hears the first words roughly a second before the
model has finished deciding which commands to send.

**Containment.** Nothing the model produces reaches a relay without passing the
validator, and nothing the validator rejects is silently dropped -- the rejection
is spoken back to the user.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.ai.base import (
    AudioEncoding,
    LanguageModel,
    LlmMessage,
    SpeechAudio,
    SpeechSynthesizer,
)
from app.ai.prompts import build_policy_notes, build_system_prompt
from app.ai.schemas import SPEECH_FIELD, AssistantReply, response_json_schema
from app.core.errors import AppError, ProviderError, TransportError
from app.core.eventbus import TOPIC_COMMAND_REJECTED, TOPIC_CONVERSATION_TURN, EventBus
from app.core.json_stream import SentenceChunker, StreamingStringField, extract_json_object
from app.core.utils import fold, truncate
from app.domain.commands import Command, CommandPlan, DispatchReport
from app.domain.conversation import PendingConfirmation, Role
from app.domain.home import HomeConfig
from app.mqtt.bridge import DeviceBridge
from app.safety.rules import SafetyPolicy
from app.safety.validator import CommandValidator
from app.services.conversation import ConversationManager
from app.services.device_state import DeviceStateManager

log = logging.getLogger(__name__)

_AFFIRMATIVE = {
    "có", "co", "ok", "okay", "đồng ý", "dong y", "xác nhận", "xac nhan",
    "ừ", "u", "ừm", "um", "vâng", "vang", "được", "duoc", "yes", "đúng", "dung",
    "làm đi", "lam di", "tiếp tục", "tiep tuc",
}
_NEGATIVE = {
    "không", "khong", "thôi", "thoi", "huỷ", "hủy", "huy", "no", "đừng", "dung lai",
    "dừng", "bỏ qua", "bo qua", "khỏi", "khoi",
}

FALLBACK_SPEECH = "Xin lỗi, mình đang gặp sự cố khi xử lý yêu cầu. Bạn thử lại giúp mình nhé."
EMPTY_SPEECH = "Mình chưa rõ yêu cầu, bạn nói lại giúp mình nhé."


class TurnSink:
    """Where a turn's output goes. The WebSocket session and the REST handler each
    implement the parts they care about; the defaults make the rest no-ops."""

    async def assistant_delta(self, text: str) -> None: ...
    async def audio_start(self, encoding: AudioEncoding, sample_rate: int) -> None: ...
    async def audio_chunk(self, payload: bytes) -> None: ...
    async def audio_end(self) -> None: ...
    async def notice(self, code: str, message: str) -> None: ...

    @property
    def cancelled(self) -> bool:
        return False


@dataclass(slots=True)
class TurnResult:
    session_id: str
    user_text: str
    speech: str
    plan: CommandPlan | None = None
    report: DispatchReport | None = None
    needs_clarification: bool = False
    latency_ms: dict[str, float] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_text": self.user_text,
            "speech": self.speech,
            "plan": self.plan.as_dict() if self.plan else None,
            "dispatched": self.report.delivered_count if self.report else 0,
            "needs_clarification": self.needs_clarification,
            "latency_ms": {k: round(v, 1) for k, v in self.latency_ms.items()},
            "error": self.error,
        }


class _SpeechWorker:
    """Synthesises clauses in order, concurrently with the model's generation."""

    def __init__(
        self,
        tts: SpeechSynthesizer,
        sink: TurnSink,
        encoding: AudioEncoding,
        sample_rate: int,
        chunk_bytes: int,
    ) -> None:
        self._tts = tts
        self._sink = sink
        self._encoding = encoding
        self._sample_rate = sample_rate
        self._chunk_bytes = chunk_bytes
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._task = asyncio.create_task(self._run(), name="tts-worker")
        self._started = False
        self.first_audio_ms: float | None = None
        self.failed: str | None = None
        self.clauses = 0
        self.audio_bytes = 0
        self._t0 = time.monotonic()

    @property
    def done(self) -> bool:
        return self._task.done()

    def submit(self, clause: str) -> None:
        if clause.strip():
            self._queue.put_nowait(clause.strip())

    async def finish(self) -> None:
        self._queue.put_nowait(None)
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        if self._started:
            with contextlib.suppress(Exception):
                await self._sink.audio_end()

    async def abort(self) -> None:
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task

    async def _run(self) -> None:
        while True:
            clause = await self._queue.get()
            if clause is None:
                return
            if self._sink.cancelled:
                continue  # drain without speaking: the user interrupted
            try:
                audio = await self._tts.synthesize(
                    clause, encoding=self._encoding, sample_rate=self._sample_rate
                )
            except AppError as exc:
                self.failed = exc.message
                log.warning("tts failed", extra={"error": exc.message})
                continue
            except Exception as exc:  # noqa: BLE001
                self.failed = str(exc)
                log.exception("tts crashed")
                continue
            await self._emit(audio)

    async def _emit(self, audio: SpeechAudio) -> None:
        if audio.is_empty or self._sink.cancelled:
            return
        if not self._started:
            self._started = True
            self.first_audio_ms = (time.monotonic() - self._t0) * 1000.0
            await self._sink.audio_start(audio.encoding, audio.sample_rate)
        payload = audio.audio
        self.clauses += 1
        self.audio_bytes += len(payload)
        size = self._chunk_bytes if audio.encoding is AudioEncoding.PCM16 else len(payload)
        for start in range(0, len(payload), max(1, size)):
            if self._sink.cancelled:
                return
            await self._sink.audio_chunk(payload[start : start + max(1, size)])


class Orchestrator:
    def __init__(
        self,
        *,
        home: HomeConfig,
        policy: SafetyPolicy,
        llm: LanguageModel,
        tts: SpeechSynthesizer,
        validator: CommandValidator,
        state: DeviceStateManager,
        conversations: ConversationManager,
        bridge: DeviceBridge,
        bus: EventBus,
        history_turns: int = 8,
        tts_chunk_bytes: int = 3200,
        sample_rate: int = 16000,
    ) -> None:
        self._home = home
        self._policy = policy
        self._llm = llm
        self._tts = tts
        self._validator = validator
        self._state = state
        self._conversations = conversations
        self._bridge = bridge
        self._bus = bus
        self._history_turns = history_turns
        self._tts_chunk_bytes = tts_chunk_bytes
        self._sample_rate = sample_rate
        self._policy_notes = build_policy_notes(home, policy)

    # ------------------------------------------------------------------ API
    async def run_turn(
        self,
        *,
        session_id: str,
        user_text: str,
        room: str | None = None,
        sink: TurnSink | None = None,
        speak: bool = True,
        encoding: AudioEncoding = AudioEncoding.PCM16,
        sample_rate: int | None = None,
    ) -> TurnResult:
        sink = sink or TurnSink()
        started = time.monotonic()
        latency: dict[str, float] = {}
        user_text = (user_text or "").strip()

        if not user_text:
            return TurnResult(session_id=session_id, user_text="", speech=EMPTY_SPEECH)

        ctx = await self._conversations.get(session_id)
        if room and ctx.room != room:
            ctx.room = room
            await self._conversations.save(ctx)

        # A parked sensitive action short-circuits the model entirely.
        if ctx.pending is not None:
            decided = await self._resolve_pending(session_id, user_text, ctx.pending, sink, speak,
                                                  encoding, sample_rate or self._sample_rate)
            if decided is not None:
                decided.latency_ms["total"] = (time.monotonic() - started) * 1000.0
                return decided

        await self._conversations.append_user(session_id, user_text)

        snapshot = await self._state.snapshot()
        now = self._validator.local_now()
        quiet = self._validator.in_quiet_hours(now)
        system = build_system_prompt(
            self._home,
            state_text=self._state.describe(snapshot),
            now=now,
            current_room=ctx.room or room,
            quiet_hours=quiet,
            policy_notes=self._policy_notes,
        )
        messages = self._history(session_id, ctx, user_text)

        worker = (
            _SpeechWorker(
                self._tts,
                sink,
                encoding,
                sample_rate or self._sample_rate,
                self._tts_chunk_bytes,
            )
            if speak
            else None
        )

        try:
            return await self._complete_turn(
                session_id=session_id,
                user_text=user_text,
                system=system,
                messages=messages,
                snapshot=snapshot,
                now=now,
                sink=sink,
                worker=worker,
                latency=latency,
                started=started,
            )
        finally:
            # Cancelling a turn (barge-in, client disconnect) must not leave the
            # synthesis task blocked on its queue for the life of the process.
            if worker is not None and not worker.done:
                await worker.abort()

    async def _complete_turn(
        self,
        *,
        session_id: str,
        user_text: str,
        system: str,
        messages: list[LlmMessage],
        snapshot,
        now,
        sink: TurnSink,
        worker: _SpeechWorker | None,
        latency: dict[str, float],
        started: float,
    ) -> TurnResult:
        raw, speech, first_token_ms, stream_error = await self._stream_reply(
            system=system, messages=messages, sink=sink, worker=worker
        )
        if first_token_ms is not None:
            latency["llm_first_token"] = first_token_ms

        reply = self._parse_reply(raw, speech)
        speech = (reply.speech or speech or "").strip()

        plan: CommandPlan | None = None
        report: DispatchReport | None = None
        addendum = ""

        if stream_error and not speech:
            speech = FALLBACK_SPEECH
            await sink.notice("llm_error", stream_error)
        elif not speech:
            speech = EMPTY_SPEECH

        if not stream_error:
            commands = self._collect_commands(reply)
            if commands:
                plan = self._validator.validate(
                    commands, session_id=session_id, snapshot=snapshot, now=now
                )
                report, addendum = await self._apply_plan(session_id, plan, speech)

        if addendum:
            speech = f"{speech} {addendum}".strip()
            if worker is not None:
                worker.submit(addendum)

        if worker is not None:
            await worker.finish()
            if worker.first_audio_ms is not None:
                latency["first_audio"] = worker.first_audio_ms
            if worker.failed:
                await sink.notice("tts_error", worker.failed)

        latency["total"] = (time.monotonic() - started) * 1000.0

        await self._conversations.append_assistant(
            session_id,
            speech,
            commands=[c.model_dump() for c in (plan.accepted if plan else ())],
            latency_ms=latency,
        )
        result = TurnResult(
            session_id=session_id,
            user_text=user_text,
            speech=speech,
            plan=plan,
            report=report,
            needs_clarification=reply.needs_clarification,
            latency_ms=latency,
            error=stream_error,
        )
        await self._bus.publish(TOPIC_CONVERSATION_TURN, result.as_dict())
        log.info(
            "turn complete",
            extra={
                "session_id": session_id,
                "plan": plan.summary() if plan else "no commands",
                "latency_ms": {k: round(v) for k, v in latency.items()},
                # Spoken reply as it actually left the server. Zero bytes with a
                # non-empty speech means synthesis produced nothing.
                "speech_chars": len(speech or ""),
                "tts_clauses": worker.clauses if worker else 0,
                "tts_audio_bytes": worker.audio_bytes if worker else 0,
                "tts_error": worker.failed if worker and worker.failed else None,
            },
        )
        return result

    # --------------------------------------------------------------- stages
    def _history(self, session_id: str, ctx, user_text: str) -> list[LlmMessage]:
        messages = [
            LlmMessage(role="user" if t.role is Role.USER else "assistant", content=t.text)
            for t in ctx.recent(self._history_turns)
            if t.text
        ]
        if not messages or messages[-1].content != user_text:
            messages.append(LlmMessage(role="user", content=user_text))
        return messages

    async def _stream_reply(
        self,
        *,
        system: str,
        messages: list[LlmMessage],
        sink: TurnSink,
        worker: _SpeechWorker | None,
    ) -> tuple[str, str, float | None, str | None]:
        """Consume the model stream, forwarding ``speech`` deltas as they decode."""
        extractor = StreamingStringField(SPEECH_FIELD)
        chunker = SentenceChunker()
        parts: list[str] = []
        first_token_ms: float | None = None
        started = time.monotonic()
        error: str | None = None

        try:
            async for delta in self._llm.stream(
                system=system, messages=messages, json_schema=response_json_schema()
            ):
                if first_token_ms is None:
                    first_token_ms = (time.monotonic() - started) * 1000.0
                if sink.cancelled:
                    break
                parts.append(delta)
                spoken = extractor.feed(delta)
                if not spoken:
                    continue
                await sink.assistant_delta(spoken)
                if worker is not None:
                    for clause in chunker.feed(spoken):
                        worker.submit(clause)
        except asyncio.CancelledError:
            raise
        except AppError as exc:
            error = exc.message
            log.warning("llm stream failed", extra={"error": exc.message})
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            log.exception("llm stream crashed")

        if worker is not None:
            tail = chunker.flush()
            if tail:
                worker.submit(tail)

        return "".join(parts), extractor.value.strip(), first_token_ms, error

    @staticmethod
    def _parse_reply(raw: str, streamed_speech: str) -> AssistantReply:
        """Recover the structured answer; a malformed object still yields the speech."""
        if raw.strip():
            try:
                return AssistantReply.model_validate(extract_json_object(raw))
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "model output was not valid AssistantReply",
                    extra={"error": str(exc), "raw": truncate(raw, 400)},
                )
        return AssistantReply(speech=streamed_speech, commands=[])

    def _collect_commands(self, reply: AssistantReply) -> list[Command]:
        commands = reply.to_commands()
        if reply.scene:
            commands = self._validator.expand_scene(reply.scene) + commands
        return commands

    async def _apply_plan(
        self, session_id: str, plan: CommandPlan, speech: str
    ) -> tuple[DispatchReport | None, str]:
        """Dispatch what was accepted and build the spoken addendum for the rest."""
        notes: list[str] = []
        report: DispatchReport | None = None

        if plan.accepted:
            try:
                report = await self._bridge.dispatch(plan)
            except TransportError as exc:
                notes.append("Hiện mình không gửi được lệnh xuống thiết bị.")
                log.warning("dispatch failed", extra={"error": exc.message})
            else:
                if report.failed:
                    failed_ids = ", ".join(
                        sorted({self._device_name(r.command.device_id) for r in report.failed})
                    )
                    notes.append(f"Riêng {failed_ids} chưa nhận được lệnh.")

        for adjust in plan.accepted:
            for note in adjust.notes:
                notes.append(f"Lưu ý: {note}.")

        if plan.pending_confirmation:
            prompt = plan.notes[0] if plan.notes else "Bạn xác nhận thực hiện chứ?"
            await self._conversations.set_pending(
                session_id,
                PendingConfirmation(
                    plan_id=plan.id,
                    prompt=prompt,
                    commands=[c.model_dump() for c in plan.pending_confirmation],
                ),
            )
            notes.append(prompt)

        if plan.rejected:
            await self._bus.publish(
                TOPIC_COMMAND_REJECTED,
                {"session_id": session_id, "plan_id": plan.id,
                 "rejected": [r.as_dict() for r in plan.rejected]},
            )
            # Speak the first rejection only: a list of error codes is not a reply.
            notes.append(plan.rejected[0].message)

        addendum = " ".join(dict.fromkeys(n for n in notes if n and n not in speech))
        return report, addendum.strip()

    # --------------------------------------------------------- confirmation
    async def _resolve_pending(
        self,
        session_id: str,
        user_text: str,
        pending: PendingConfirmation,
        sink: TurnSink,
        speak: bool,
        encoding: AudioEncoding,
        sample_rate: int,
    ) -> TurnResult | None:
        """Handle "vâng" / "thôi" against a parked sensitive action.

        Returns ``None`` when the utterance is neither, so the turn proceeds
        normally and the pending action simply expires with the conversation.
        """
        token = fold(user_text)
        affirmative = token in _AFFIRMATIVE or any(token.startswith(w + " ") for w in _AFFIRMATIVE)
        negative = token in _NEGATIVE or any(token.startswith(w + " ") for w in _NEGATIVE)
        if not affirmative and not negative:
            return None

        await self._conversations.append_user(session_id, user_text)
        await self._conversations.set_pending(session_id, None)

        if negative:
            speech = "Được, mình đã huỷ thao tác đó."
            await self._speak_simple(speech, sink, speak, encoding, sample_rate)
            await self._conversations.append_assistant(session_id, speech)
            return TurnResult(session_id=session_id, user_text=user_text, speech=speech)

        commands = [
            Command(
                device_id=c["device_id"],
                capability=c["capability"],
                value=c["value"],
                delay_s=c.get("delay_s", 0.0),
                reason="user-confirmed",
            )
            for c in pending.commands
        ]
        snapshot = await self._state.snapshot()
        plan = self._validator.validate(
            commands, session_id=session_id, snapshot=snapshot, skip_confirmation=True
        )
        report, addendum = await self._apply_plan(session_id, plan, "")
        speech = ("Đã thực hiện. " + addendum).strip() if plan.accepted else (
            addendum or "Mình không thực hiện được thao tác đó."
        )
        await self._speak_simple(speech, sink, speak, encoding, sample_rate)
        await self._conversations.append_assistant(
            session_id, speech, commands=[c.model_dump() for c in plan.accepted]
        )
        return TurnResult(
            session_id=session_id, user_text=user_text, speech=speech, plan=plan, report=report
        )

    async def _speak_simple(
        self,
        text: str,
        sink: TurnSink,
        speak: bool,
        encoding: AudioEncoding,
        sample_rate: int,
    ) -> None:
        await sink.assistant_delta(text)
        if not speak:
            return
        worker = _SpeechWorker(self._tts, sink, encoding, sample_rate, self._tts_chunk_bytes)
        try:
            worker.submit(text)
            await worker.finish()
        finally:
            if not worker.done:
                await worker.abort()

    # -------------------------------------------------------------- helpers
    def _device_name(self, device_id: str) -> str:
        device = self._home.device_map.get(device_id)
        return device.name if device else device_id

    async def execute_commands(
        self, commands: list[Command], *, session_id: str = "api"
    ) -> tuple[CommandPlan, DispatchReport | None]:
        """Direct control path for the dashboard: same validator, no model."""
        snapshot = await self._state.snapshot()
        plan = self._validator.validate(commands, session_id=session_id, snapshot=snapshot)
        report = await self._bridge.dispatch(plan) if plan.accepted else None
        if plan.rejected:
            await self._bus.publish(
                TOPIC_COMMAND_REJECTED,
                {"session_id": session_id, "plan_id": plan.id,
                 "rejected": [r.as_dict() for r in plan.rejected]},
            )
        return plan, report


__all__ = ["Orchestrator", "TurnResult", "TurnSink", "ProviderError"]
