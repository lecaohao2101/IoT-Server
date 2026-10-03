"""Command objects: what the model asks for, what the validator allows, what MQTT sends."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.utils import new_id, utc_iso


class Command(BaseModel):
    """A single intended change, as proposed by the LLM or by the REST API."""

    model_config = ConfigDict(extra="ignore")

    device_id: str = Field(description="Exact device id from the catalogue")
    capability: str = Field(description="Exact capability name on that device")
    value: Any = Field(description="Target value, matching the capability's domain")
    delay_s: float = Field(default=0.0, ge=0.0, le=3600.0)
    reason: str | None = Field(default=None, description="Short justification, for the audit log")

    def key(self) -> tuple[str, str]:
        return (self.device_id, self.capability)


class RejectionCode(str, Enum):
    UNKNOWN_DEVICE = "unknown_device"
    UNKNOWN_CAPABILITY = "unknown_capability"
    READ_ONLY = "read_only"
    INVALID_VALUE = "invalid_value"
    OUT_OF_POLICY = "out_of_policy"
    QUIET_HOURS = "quiet_hours"
    NEEDS_CONFIRMATION = "needs_confirmation"
    RATE_LIMITED = "rate_limited"
    PLAN_TOO_LARGE = "plan_too_large"
    DEVICE_OFFLINE = "device_offline"
    DUPLICATE = "duplicate"


class ValidatedCommand(BaseModel):
    """A command that survived the rule engine, with its value normalised."""

    model_config = ConfigDict(extra="forbid")

    device_id: str
    capability: str
    value: Any
    delay_s: float = 0.0
    reason: str | None = None
    notes: tuple[str, ...] = ()
    original_value: Any = None

    @property
    def was_adjusted(self) -> bool:
        return bool(self.notes)


class RejectedCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: Command
    code: RejectionCode
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.command.device_id,
            "capability": self.command.capability,
            "value": self.command.value,
            "code": self.code.value,
            "message": self.message,
        }


class CommandPlan(BaseModel):
    """The validator's verdict over one batch of commands."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("plan"))
    created_at: str = Field(default_factory=utc_iso)
    accepted: tuple[ValidatedCommand, ...] = ()
    rejected: tuple[RejectedCommand, ...] = ()
    pending_confirmation: tuple[ValidatedCommand, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.accepted and not self.pending_confirmation

    @property
    def has_rejections(self) -> bool:
        return bool(self.rejected)

    def summary(self) -> str:
        """One line for the log and for appending to the spoken reply when needed."""
        parts = []
        if self.accepted:
            parts.append(f"{len(self.accepted)} accepted")
        if self.pending_confirmation:
            parts.append(f"{len(self.pending_confirmation)} awaiting confirmation")
        if self.rejected:
            parts.append(f"{len(self.rejected)} rejected")
        return ", ".join(parts) or "no commands"

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.id,
            "created_at": self.created_at,
            "accepted": [c.model_dump() for c in self.accepted],
            "pending_confirmation": [c.model_dump() for c in self.pending_confirmation],
            "rejected": [r.as_dict() for r in self.rejected],
            "notes": list(self.notes),
        }


class DispatchResult(BaseModel):
    """Outcome of pushing one accepted command onto the transport."""

    model_config = ConfigDict(extra="forbid")

    command: ValidatedCommand
    delivered: bool
    topic: str | None = None
    error: str | None = None


class DispatchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    results: tuple[DispatchResult, ...] = ()

    @property
    def delivered_count(self) -> int:
        return sum(1 for r in self.results if r.delivered)

    @property
    def failed(self) -> list[DispatchResult]:
        return [r for r in self.results if not r.delivered]
