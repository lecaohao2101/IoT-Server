"""REST endpoints for voice biometrics and speaker enrollment."""

from __future__ import annotations

import base64
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import Container, CurrentPrincipal

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/speakers", tags=["speakers"])


class SpeakerSummary(BaseModel):
    id: str
    name: str
    role: str
    samples_count: int
    created_at: str


class SpeakersListResponse(BaseModel):
    enabled: bool
    threshold: float
    total: int
    speakers: list[SpeakerSummary]


class EnrollSpeakerRequest(BaseModel):
    speaker_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    role: Literal["owner", "member", "guest"] = "member"
    audio_base64: str = Field(..., description="Base64 encoded 16 kHz mono 16-bit PCM audio")


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


@router.get("", response_model=SpeakersListResponse)
async def list_speakers(container: Container, principal: CurrentPrincipal) -> SpeakersListResponse:
    """List all enrolled speaker profiles and verification configuration."""
    manager = container.speaker_manager
    speakers = [
        SpeakerSummary(
            id=s.id,
            name=s.name,
            role=s.role,
            samples_count=s.samples_count,
            created_at=s.created_at,
        )
        for s in manager.list_speakers()
    ]
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
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return SpeakerSummary(
        id=profile.id,
        name=profile.name,
        role=profile.role,
        samples_count=profile.samples_count,
        created_at=profile.created_at,
    )


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
