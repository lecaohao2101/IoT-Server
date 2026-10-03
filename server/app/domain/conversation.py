"""Conversation memory: the short-term context that makes follow-ups work.

"Bật đèn phòng khách" then "sáng hơn chút" only resolves if the second turn can see
the first. We keep a bounded, TTL'd transcript per session plus the last plan, so a
"vâng" can confirm a pending sensitive action.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.utils import new_id, utc_iso


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("turn"))
    role: Role
    text: str
    ts: str = Field(default_factory=utc_iso)
    #: Commands dispatched as part of an assistant turn, for "undo that" and audits.
    commands: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: dict[str, float] = Field(default_factory=dict)

    def as_prompt_line(self) -> str:
        speaker = "Người dùng" if self.role is Role.USER else "Trợ lý"
        return f"{speaker}: {self.text}"


class PendingConfirmation(BaseModel):
    """A sensitive plan parked until the user says yes."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    prompt: str
    commands: list[dict[str, Any]]
    created_at: str = Field(default_factory=utc_iso)
    expires_at: str | None = None


class ConversationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    room: str | None = None
    turns: list[Turn] = Field(default_factory=list)
    pending: PendingConfirmation | None = None
    updated_at: str = Field(default_factory=utc_iso)

    def recent(self, limit: int) -> list[Turn]:
        return self.turns[-limit:] if limit > 0 else []

    def transcript(self, limit: int = 8) -> str:
        return "\n".join(turn.as_prompt_line() for turn in self.recent(limit))
