"""The Command Plan gate.

Everything the LLM proposes lands here before anything touches MQTT. The validator
is deterministic, has no network calls and no model in the loop: given the same
commands, catalogue, policy and clock it always reaches the same verdict.

Order of checks per command (first failure wins):
  1. the device exists (with one alias-repair attempt)
  2. the capability exists and is writable
  3. the value fits the capability's own domain  -> may clamp
  4. the value fits the policy's narrower limits -> may clamp
  5. no forbid rule matches
  6. the device is reachable, if the policy demands it
  7. the per-device command budget is not exhausted
  8. sensitive actions are parked for confirmation rather than dispatched
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.core.ratelimit import KeyedRateLimiter
from app.core.utils import fold
from app.domain.commands import (
    Command,
    CommandPlan,
    RejectedCommand,
    RejectionCode,
    ValidatedCommand,
)
from app.domain.home import CapabilityKind, Device, HomeConfig
from app.domain.state import HomeSnapshot
from app.safety.rules import SafetyPolicy

log = logging.getLogger(__name__)


def _zone(name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - missing tzdata on a bare Windows host
        log.warning("timezone %s unavailable, falling back to UTC", name)
        return UTC


class CommandValidator:
    def __init__(
        self,
        home: HomeConfig,
        policy: SafetyPolicy,
        tz_name: str | None = None,
    ) -> None:
        self._home = home
        self._policy = policy
        self._tz = _zone(tz_name or home.timezone)
        limits = policy.rate_limits
        self._device_limiter = KeyedRateLimiter(
            capacity=limits.device_burst,
            refill_per_second=limits.commands_per_device_per_minute / 60.0,
        )
        self._session_limiter = KeyedRateLimiter(
            capacity=limits.session_burst,
            refill_per_second=limits.plans_per_session_per_minute / 60.0,
        )

    # ------------------------------------------------------------------ API
    def local_now(self) -> datetime:
        return datetime.now(self._tz)

    def in_quiet_hours(self, now: datetime | None = None) -> bool:
        return self._policy.quiet_hours.contains(now or self.local_now())

    def expand_scene(self, scene_id: str) -> list[Command]:
        """Turn a scene reference into the concrete commands it stands for."""
        scene = self._home.scene_map.get(scene_id) or self._home.resolve_scene(scene_id)
        if scene is None:
            return []
        return [Command(**{**action, "reason": action.get("reason") or f"scene:{scene.id}"})
                for action in scene.actions]

    def validate(
        self,
        commands: list[Command],
        *,
        session_id: str = "anonymous",
        snapshot: HomeSnapshot | None = None,
        now: datetime | None = None,
        skip_confirmation: bool = False,
    ) -> CommandPlan:
        now = now or self.local_now()
        quiet = self._policy.quiet_hours.contains(now)

        accepted: list[ValidatedCommand] = []
        rejected: list[RejectedCommand] = []
        pending: list[ValidatedCommand] = []
        notes: list[str] = []

        if not commands:
            return CommandPlan()

        if not self._session_limiter.allow(session_id):
            return CommandPlan(
                rejected=tuple(
                    RejectedCommand(
                        command=c,
                        code=RejectionCode.RATE_LIMITED,
                        message="Quá nhiều yêu cầu trong thời gian ngắn.",
                    )
                    for c in commands
                ),
                notes=("session rate limit exceeded",),
            )

        head, overflow = commands[: self._policy.max_commands_per_plan], commands[
            self._policy.max_commands_per_plan :
        ]
        for extra in overflow:
            rejected.append(
                RejectedCommand(
                    command=extra,
                    code=RejectionCode.PLAN_TOO_LARGE,
                    message=(
                        f"Một lượt chỉ thực hiện tối đa "
                        f"{self._policy.max_commands_per_plan} lệnh."
                    ),
                )
            )
        if overflow:
            notes.append(f"dropped {len(overflow)} command(s) over the per-plan limit")

        deduped, duplicates = self._dedupe(head)
        for dup in duplicates:
            rejected.append(
                RejectedCommand(
                    command=dup,
                    code=RejectionCode.DUPLICATE,
                    message="Lệnh trùng lặp, chỉ giữ lệnh cuối cùng.",
                )
            )

        for command in deduped:
            outcome = self._validate_one(command, quiet=quiet, snapshot=snapshot)
            if isinstance(outcome, RejectedCommand):
                rejected.append(outcome)
                continue
            validated, needs_confirm, confirm_prompt = outcome
            if needs_confirm and not skip_confirmation:
                pending.append(validated)
                notes.append(confirm_prompt or "cần xác nhận")
                continue
            if not self._device_limiter.allow(validated.device_id):
                rejected.append(
                    RejectedCommand(
                        command=command,
                        code=RejectionCode.RATE_LIMITED,
                        message="Thiết bị đang nhận lệnh quá nhanh, thử lại sau giây lát.",
                    )
                )
                continue
            accepted.append(validated)
            notes.extend(validated.notes)

        plan = CommandPlan(
            accepted=tuple(accepted),
            rejected=tuple(rejected),
            pending_confirmation=tuple(pending),
            notes=tuple(dict.fromkeys(notes)),  # de-duplicated, order preserved
        )
        log.info(
            "command plan validated",
            extra={
                "plan_id": plan.id,
                "session_id": session_id,
                "summary": plan.summary(),
                "quiet_hours": quiet,
            },
        )
        return plan

    # -------------------------------------------------------------- internals
    @staticmethod
    def _dedupe(commands: list[Command]) -> tuple[list[Command], list[Command]]:
        """Keep the last command per (device, capability); report the shadowed ones."""
        last_index: dict[tuple[str, str], int] = {}
        for idx, command in enumerate(commands):
            last_index[command.key()] = idx
        keep = [c for i, c in enumerate(commands) if last_index[c.key()] == i]
        drop = [c for i, c in enumerate(commands) if last_index[c.key()] != i]
        return keep, drop

    def _resolve_device(self, device_id: str) -> Device | None:
        device = self._home.device_map.get(device_id)
        if device is not None:
            return device
        # One repair attempt: the model occasionally answers with a display name.
        candidates = self._home.resolve_devices(device_id)
        if len(candidates) == 1:
            log.info(
                "repaired device id from alias",
                extra={"given": device_id, "resolved": candidates[0].id},
            )
            return candidates[0]
        return None

    def _validate_one(
        self,
        command: Command,
        *,
        quiet: bool,
        snapshot: HomeSnapshot | None,
    ) -> RejectedCommand | tuple[ValidatedCommand, bool, str | None]:
        device = self._resolve_device(command.device_id)
        if device is None:
            return RejectedCommand(
                command=command,
                code=RejectionCode.UNKNOWN_DEVICE,
                message=f"Không có thiết bị '{command.device_id}' trong nhà.",
            )

        capability = device.capabilities.get(command.capability)
        if capability is None:
            return RejectedCommand(
                command=command,
                code=RejectionCode.UNKNOWN_CAPABILITY,
                message=(
                    f"Thiết bị '{device.name}' không có thuộc tính "
                    f"'{command.capability}'."
                ),
            )
        if capability.read_only:
            return RejectedCommand(
                command=command,
                code=RejectionCode.READ_ONLY,
                message=f"'{command.capability}' chỉ đọc, không thể điều khiển.",
            )

        notes: list[str] = []
        try:
            value, clamp_note = capability.coerce(command.value)
        except Exception as exc:  # noqa: BLE001 - ValidationError carries the message
            return RejectedCommand(
                command=command,
                code=RejectionCode.INVALID_VALUE,
                message=str(getattr(exc, "message", exc)),
            )
        if clamp_note:
            notes.append(f"{device.name}: {clamp_note}")

        if capability.kind is CapabilityKind.NUMBER:
            value, policy_note = self._apply_limits(device, command.capability, value, quiet)
            if policy_note:
                notes.append(f"{device.name}: {policy_note}")

        blocked = self._find_forbid(device, command.capability, value, quiet)
        if blocked is not None:
            return RejectedCommand(
                command=command,
                code=RejectionCode.QUIET_HOURS if quiet and blocked[1] else RejectionCode.OUT_OF_POLICY,
                message=blocked[0],
            )

        if (
            self._policy.require_device_online
            and snapshot is not None
            and not snapshot.get(device.id).online
        ):
            return RejectedCommand(
                command=command,
                code=RejectionCode.DEVICE_OFFLINE,
                message=f"'{device.name}' hiện không kết nối.",
            )

        delay = min(max(command.delay_s, 0.0), self._policy.max_delay_s)
        if delay != command.delay_s:
            notes.append(f"{device.name}: hẹn giờ rút xuống {delay:g}s")

        validated = ValidatedCommand(
            device_id=device.id,
            capability=command.capability,
            value=value,
            delay_s=delay,
            reason=command.reason,
            notes=tuple(notes),
            original_value=command.value,
        )

        needs_confirm, prompt = self._needs_confirmation(device, command.capability, value)
        return validated, needs_confirm, prompt

    def _apply_limits(
        self, device: Device, capability: str, value: Any, quiet: bool
    ) -> tuple[Any, str | None]:
        note: str | None = None
        for rule in self._policy.limits_for(device, capability, quiet):
            if rule.minimum is not None and value < rule.minimum:
                note = rule.message or f"{capability} nâng lên mức tối thiểu {rule.minimum:g}"
                value = rule.minimum
            if rule.maximum is not None and value > rule.maximum:
                note = rule.message or f"{capability} hạ xuống mức tối đa {rule.maximum:g}"
                value = rule.maximum
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return value, note

    def _find_forbid(
        self, device: Device, capability: str, value: Any, quiet: bool
    ) -> tuple[str, bool] | None:
        for rule in self._policy.forbid_for(device, capability, quiet):
            if rule.value is None or _same_value(rule.value, value):
                return rule.message, rule.quiet_hours_only
        return None

    def _needs_confirmation(
        self, device: Device, capability: str, value: Any
    ) -> tuple[bool, str | None]:
        # A rule written for this exact action phrases the question better than
        # anything generated, so policy prompts win over the generic fallback.
        for rule in self._policy.confirm_for(device, capability):
            if rule.value is None or _same_value(rule.value, value):
                return True, rule.prompt
        if device.capabilities[capability].sensitive:
            return True, f"Xác nhận đổi '{capability}' của {device.name} thành {_speak(value)}?"
        return False, None


def _speak(value: Any) -> str:
    """Render a value for a spoken prompt -- never leak Python's `True`/`False`."""
    if isinstance(value, bool):
        return "bật" if value else "tắt"
    return str(value)


def _same_value(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return fold(str(a)) == fold(str(b))
