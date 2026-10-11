"""REST endpoints for voice biometrics and speaker enrollment."""

from __future__ import annotations

import base64
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import Container, CurrentPrincipal
from app.services.speaker import SpeakerManager, SpeakerProfile, enrollment_phrases

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/speakers", tags=["speakers"])


class SpeakerSummary(BaseModel):
    id: str
    name: str
    role: str
    samples_count: int
    created_at: str
    #: False while the profile still owes samples. The app keeps the enrollment
    #: screen up until this turns true.
    complete: bool = True
    remaining_samples: int = 0


class SpeakersListResponse(BaseModel):
    enabled: bool
    threshold: float
    total: int
    speakers: list[SpeakerSummary]


class EnrollmentPhrase(BaseModel):
    index: int
    text: str


class EnrollmentPlanResponse(BaseModel):
    """Everything the app needs to put an enrollment screen on the phone."""

    #: Whether this device must enroll before it is allowed to talk. False does
    #: not mean enrollment is pointless, only that it is not blocking.
    required: bool
    #: Whether ``speaker_id`` (when given) has finished reading its sentences.
    satisfied: bool
    assistant_name: str
    wake_word_enabled: bool
    verification_enabled: bool
    required_samples: int
    completed_samples: int
    #: Read these aloud, in order; one recording each.
    phrases: list[EnrollmentPhrase]
    #: The format the recordings must arrive in. Getting this wrong produces a
    #: profile that never matches anybody, silently.
    sample_rate: int
    channels: int
    encoding: str
    min_sample_ms: int
    enrolled_total: int


class EnrollSpeakerRequest(BaseModel):
    speaker_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    role: Literal["owner", "member", "guest"] = "member"
    audio_base64: str = Field(..., description="Base64 encoded 16 kHz mono 16-bit PCM audio")
    #: Which prompt was read, so an interrupted enrollment can resume where it
    #: stopped instead of starting the user over.
    phrase_index: int | None = Field(default=None, ge=0, le=64)


class VerifySpeakerRequest(BaseModel):
    audio_base64: str = Field(..., description="Base64 encoded 16 kHz mono 16-bit PCM audio")
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class VerifySpeakerResponse(BaseModel):
    verified: bool
    speaker_id: str | None = None
    speaker_name: str | None = None
    role: str | None = None
    score: float
    threshold: float
    reason: str


class SpeakerSettingsUpdate(BaseModel):
    enabled: bool | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)


def _summary(manager: SpeakerManager, profile: SpeakerProfile) -> SpeakerSummary:
    return SpeakerSummary(
        id=profile.id,
        name=profile.name,
        role=profile.role,
        samples_count=profile.samples_count,
        created_at=profile.created_at,
        complete=manager.is_complete(profile),
        remaining_samples=manager.remaining_samples(profile.id),
    )


@router.get("/enrollment", response_model=EnrollmentPlanResponse)
async def enrollment_plan(
    container: Container,
    principal: CurrentPrincipal,
    speaker_id: str | None = Query(default=None, max_length=64),
) -> EnrollmentPlanResponse:
    """What this device has to record before it can give voice commands.

    The phrases come from the server rather than the app so that changing the
    assistant's name changes what people are asked to read, in one place. They
    all contain the name on purpose: the first thing a new user does is say it.
    """
    manager = container.speaker_manager
    settings = container.settings
    profile = manager.get_speaker(speaker_id) if speaker_id else None
    done = profile.samples_count if profile else 0

    return EnrollmentPlanResponse(
        required=settings.voice_enrollment_required or manager.enabled,
        satisfied=manager.is_complete(profile),
        assistant_name=settings.assistant_name,
        wake_word_enabled=settings.wake_word_enabled,
        verification_enabled=manager.enabled,
        required_samples=manager.required_samples,
        completed_samples=done,
        phrases=[
            EnrollmentPhrase(index=i, text=text)
            for i, text in enumerate(
                enrollment_phrases(settings.assistant_name, manager.required_samples)
            )
        ],
        sample_rate=settings.audio_sample_rate,
        channels=1,
        encoding="pcm16",
        #: Shorter than this and the fingerprint has too few voiced frames to
        #: mean anything -- the extractor would refuse it anyway, after the
        #: upload.
        min_sample_ms=800,
        enrolled_total=len(manager.list_speakers()),
    )


@router.get("", response_model=SpeakersListResponse)
async def list_speakers(container: Container, principal: CurrentPrincipal) -> SpeakersListResponse:
    """List all enrolled speaker profiles and verification configuration."""
    manager = container.speaker_manager
    speakers = [_summary(manager, s) for s in manager.list_speakers()]
    return SpeakersListResponse(
        enabled=manager.enabled,
        threshold=manager.threshold,
        total=len(speakers),
        speakers=speakers,
    )


@router.post("/enroll", response_model=SpeakerSummary, status_code=status.HTTP_201_CREATED)
async def enroll_speaker(
    request: EnrollSpeakerRequest,
    container: Container,
    principal: CurrentPrincipal,
) -> SpeakerSummary:
    """Enroll a new speaker voice profile or append a sample to an existing one."""
    manager = container.speaker_manager
    try:
        pcm_bytes = base64.b64decode(request.audio_base64)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Dữ liệu audio_base64 không hợp lệ: {exc}",
        ) from exc

    try:
        profile = manager.enroll(
            speaker_id=request.speaker_id,
            name=request.name,
            pcm_bytes=pcm_bytes,
            role=request.role,
            phrase_index=request.phrase_index,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return _summary(manager, profile)


@router.post("/verify", response_model=VerifySpeakerResponse)
async def verify_speaker(
    request: VerifySpeakerRequest,
    container: Container,
    principal: CurrentPrincipal,
) -> VerifySpeakerResponse:
    """Verify an audio sample against enrolled speaker profiles."""
    manager = container.speaker_manager
    try:
        pcm_bytes = base64.b64decode(request.audio_base64)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Dữ liệu audio_base64 không hợp lệ: {exc}",
        ) from exc

    result = manager.verify(pcm_bytes, threshold=request.threshold, force_match=True)
    return VerifySpeakerResponse(
        verified=result.verified,
        speaker_id=result.speaker_id,
        speaker_name=result.speaker_name,
        role=result.role,
        score=result.score,
        threshold=result.threshold,
        reason=result.reason,
    )


@router.delete("/{speaker_id}")
async def delete_speaker(
    speaker_id: str,
    container: Container,
    principal: CurrentPrincipal,
) -> dict[str, str | bool]:
    """Delete an enrolled speaker profile."""
    manager = container.speaker_manager
    deleted = manager.delete(speaker_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy hồ sơ giọng nói: {speaker_id}",
        )
    return {"deleted": True, "speaker_id": speaker_id}


@router.patch("/settings")
async def update_settings(
    update: SpeakerSettingsUpdate,
    container: Container,
    principal: CurrentPrincipal,
) -> dict[str, bool | float]:
    """Update speaker verification enabled flag and similarity threshold."""
    manager = container.speaker_manager
    if update.enabled is not None:
        manager.enabled = update.enabled
    if update.threshold is not None:
        manager.threshold = update.threshold
    return {"enabled": manager.enabled, "threshold": manager.threshold}
