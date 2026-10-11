"""Everything that must be true before an utterance becomes a command.

Two questions, in this order, for every recognised sentence on every input path
-- the ESP32 microphone, the mobile push-to-talk socket and the firmware's
``/upload-audio``:

1. **Was it said to us?** The name has to be there, or the conversation opened
   by an earlier summon has to still be running. Cheap: pure text.
2. **Who said it?** The acoustic fingerprint has to match an enrolled profile.
   Expensive: an FFT over the whole utterance, which is why it runs second.

A single gate rather than one check per transport, because the two paths
diverging is exactly how a device ends up quietly unguarded -- the ESP32 posts
to an unauthenticated route, so this is the only thing standing between a
passer-by and the lights.

The gate holds the conversation window: once summoned, follow-up sentences in
the next ``wake_follow_up_s`` seconds need no name. Without it every single turn
of a conversation starts with "Anh Thạc Sĩ", which nobody does twice.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from app.config import Settings
from app.services.speaker import SpeakerManager, VerificationResult
from app.services.wakeword import WakeMatch, WakeWordDetector

log = logging.getLogger(__name__)

#: Beyond this many tracked sessions the expired ones are swept. A session is
#: one dict entry; the sweep exists so a long-lived process does not accumulate
#: one per device that ever connected.
_PRUNE_AT = 64


class GateOutcome(str, Enum):
    ALLOW = "allow"
    #: Not addressed to us. The assistant says nothing at all -- answering
    #: "I didn't hear my name" to every overheard sentence is worse than the
    #: problem it solves.
    IGNORE = "ignore"
    #: The name and nothing else. Acknowledge and hold the conversation open.
    SUMMON = "summon"
    DENY_SPEAKER = "deny_speaker"
    DENY_ENROLLMENT = "deny_enrollment"


@dataclass(slots=True)
class GateResult:
    outcome: GateOutcome
    #: What the model should reason about: the utterance without the name.
    text: str = ""
    #: What to say back. Empty means stay silent.
    speech: str = ""
    code: str = ""
    wake: WakeMatch | None = None
    verification: VerificationResult | None = None

    @property
    def allowed(self) -> bool:
        return self.outcome is GateOutcome.ALLOW

    @property
    def speaker_name(self) -> str | None:
        return self.verification.speaker_name if self.verification else None

    def as_log_fields(self) -> dict[str, object]:
        fields: dict[str, object] = {"outcome": self.outcome.value}
        if self.code:
            fields["reason"] = self.code
        if self.wake is not None:
            fields["wake_matched"] = self.wake.matched
            fields["wake_score"] = self.wake.score
            if self.wake.alias:
                fields["wake_alias"] = self.wake.alias
        if self.verification is not None:
            fields["speaker"] = self.verification.speaker_name
            fields["speaker_score"] = self.verification.score
        return fields


class VoiceGate:
    def __init__(
        self,
        *,
        detector: WakeWordDetector,
        speakers: SpeakerManager,
        settings: Settings,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._detector = detector
        self._speakers = speakers
        self._s = settings
        self._clock = clock
        self._open_until: dict[str, float] = {}

    # ------------------------------------------------------------- properties
    @property
    def detector(self) -> WakeWordDetector:
        return self._detector

    @property
    def assistant_name(self) -> str:
        return self._detector.name

    @property
    def follow_up_s(self) -> float:
        return max(0.0, self._s.wake_follow_up_s)

    # ---------------------------------------------------------------- window
    def is_open(self, session_id: str) -> bool:
        """True while a conversation started by a summon is still running."""
        until = self._open_until.get(session_id)
        return until is not None and self._clock() < until

    def open_window(self, session_id: str) -> None:
        if self.follow_up_s <= 0:
            return
        if len(self._open_until) >= _PRUNE_AT:
            self._prune()
        self._open_until[session_id] = self._clock() + self.follow_up_s

    def close_window(self, session_id: str) -> None:
        self._open_until.pop(session_id, None)

    def _prune(self) -> None:
        now = self._clock()
        for key in [k for k, until in self._open_until.items() if until <= now]:
            del self._open_until[key]

    # ------------------------------------------------------------- decision
    def evaluate(
        self,
        *,
        session_id: str,
        text: str,
        audio: bytes = b"",
        has_pending: bool = False,
    ) -> GateResult:
        """Decide what to do with one recognised utterance.

        ``has_pending`` is a parked sensitive action waiting for a yes or a no.
        Such a reply bypasses the name: the assistant asked the question a second
        ago, and making the user re-summon in order to answer it would be a way
        to leave a door unlocked by accident.
        """
        utterance = (text or "").strip()
        if not utterance:
            return GateResult(outcome=GateOutcome.IGNORE, code="empty")

        wake = self._detector.detect(utterance)
        window_open = self.is_open(session_id)
        if not (wake.matched or window_open or has_pending):
            if wake.near_miss:
                # Tuning evidence: a run of these means the threshold is too
                # high, or the recogniser spells the name a way we do not know.
                log.info(
                    "wake word near miss",
                    extra={"session_id": session_id, "score": wake.score},
                )
            return GateResult(
                outcome=GateOutcome.IGNORE,
                code="wake_word_required",
                wake=wake,
            )

        # Inside the window the whole sentence is the command; the name, when it
        # is there anyway, is still stripped off before the model sees it.
        command = wake.command if wake.matched else utterance

        enrollment = self._check_enrollment()
        if enrollment is not None:
            return enrollment

        verification = self._verify(audio)
        if verification is not None and not verification.verified:
            log.warning(
                "voice command rejected -- unauthorized speaker",
                extra={
                    "session_id": session_id,
                    "reason": verification.reason,
                    "score": verification.score,
                    "threshold": verification.threshold,
                },
            )
            return GateResult(
                outcome=GateOutcome.DENY_SPEAKER,
                speech=(
                    "Xin lỗi, mình không nhận diện được giọng nói của bạn "
                    "trong danh sách người dùng được cấp quyền."
                ),
                code="unauthorized_speaker",
                wake=wake,
                verification=verification,
            )

        # Everything below here is addressed to us and allowed to speak, so the
        # conversation stays open whether or not there was a command in it.
        self.open_window(session_id)

        if not command:
            return GateResult(
                outcome=GateOutcome.SUMMON,
                speech=f"Dạ, {self.assistant_name} nghe đây. Bạn cần mình giúp gì nè?",
                code="summoned",
                wake=wake,
                verification=verification,
            )

        return GateResult(
            outcome=GateOutcome.ALLOW,
            text=command,
            wake=wake,
            verification=verification,
        )

    # --------------------------------------------------------------- helpers
    def _check_enrollment(self) -> GateResult | None:
        """Refuse a locked-down apartment that nobody has enrolled a voice on.

        Off by default: with no profiles the verifier admits everyone, which is
        what makes the offline demo work out of the box. Turning
        ``VOICE_ENROLLMENT_REQUIRED`` on turns that open door into a spoken
        instruction to go and enroll in the app.
        """
        if not (self._s.voice_enrollment_required and self._speakers.enabled):
            return None
        if self._speakers.has_enrolled_speakers:
            return None
        return GateResult(
            outcome=GateOutcome.DENY_ENROLLMENT,
            speech=(
                "Căn hộ chưa có giọng nói nào được đăng ký. Bạn hãy mở ứng dụng "
                "và đăng ký giọng nói của mình trước, rồi gọi lại mình nhé."
            ),
            code="enrollment_required",
        )

    def _verify(self, audio: bytes) -> VerificationResult | None:
        """``None`` when biometrics are not in play for this utterance."""
        if not (self._speakers.enabled and self._speakers.has_enrolled_speakers):
            return None
        if not audio:
            # A text frame or a transport that keeps no PCM. Nothing to measure,
            # and refusing on no evidence would break the typed path entirely.
            return None
        return self._speakers.verify(audio)


__all__ = ["GateOutcome", "GateResult", "VoiceGate"]
