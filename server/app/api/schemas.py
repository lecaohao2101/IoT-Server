"""Request and response bodies for the REST surface."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.state import DeviceView


class CommandRequest(BaseModel):
    """Direct control of one capability, bypassing the model but not the validator."""

    model_config = ConfigDict(extra="forbid")

    capability: str = Field(min_length=1, max_length=64)
    value: Any
    delay_s: float = Field(default=0.0, ge=0.0, le=3600.0)


class BatchCommandItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=64)
    capability: str = Field(min_length=1, max_length=64)
    value: Any
    delay_s: float = Field(default=0.0, ge=0.0, le=3600.0)


class BatchCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commands: list[BatchCommandItem] = Field(min_length=1, max_length=32)


class CommandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: dict[str, Any]
    dispatched: int = 0
    failed: list[dict[str, Any]] = Field(default_factory=list)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)
    room: str | None = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    speech: str
    plan: dict[str, Any] | None = None
    dispatched: int = 0
    needs_clarification: bool = False
    latency_ms: dict[str, float] = Field(default_factory=dict)
    error: str | None = None


class TranscriptTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    text: str
    ts: str
    commands: list[dict[str, Any]] = Field(default_factory=list)


class ChatHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    room: str | None = None
    turns: list[TranscriptTurn] = Field(default_factory=list)
    pending: dict[str, Any] | None = None


class RoomResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    device_ids: list[str] = Field(default_factory=list)


class SceneResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)


class DeviceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    devices: list[DeviceView]
    count: int


class SpeakRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    voice: str | None = None
