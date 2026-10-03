"""Authentication for HTTP and WebSocket entry points.

Two kinds of caller exist: the mobile app (shared ``API_KEY``) and an ESP32 unit
(per-device token from ``DEVICE_TOKENS``). Devices get their own credential so one
compromised unit can be revoked without re-flashing the rest of the apartment.

WebSocket clients may pass the credential as a query parameter because the ESP32
WebSocket stacks commonly cannot attach custom headers during the upgrade.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Literal

from app.config import Settings
from app.core.errors import UnauthorizedError

PrincipalKind = Literal["app", "device", "anonymous"]


@dataclass(frozen=True, slots=True)
class Principal:
    kind: PrincipalKind
    id: str

    @property
    def is_device(self) -> bool:
        return self.kind == "device"

    def __str__(self) -> str:  # pragma: no cover - logging sugar
        return f"{self.kind}:{self.id}"


ANONYMOUS = Principal(kind="anonymous", id="dev")


def _equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def extract_bearer(header_value: str | None) -> str | None:
    """Pull the credential out of an ``Authorization`` header, scheme-insensitively."""
    if not header_value:
        return None
    parts = header_value.split(None, 1)
    if len(parts) == 2 and parts[0].lower() in {"bearer", "token"}:
        return parts[1].strip()
    return header_value.strip() or None


def authenticate(
    settings: Settings,
    *,
    token: str | None,
    device_id: str | None = None,
) -> Principal:
    """Resolve a credential to a principal, or raise :class:`UnauthorizedError`.

    Auth is skipped entirely when no ``API_KEY`` is configured; ``Settings`` refuses
    to start in that state when ``APP_ENV=prod``, so this can only happen in dev.
    """
    if not settings.auth_enabled:
        return Principal(kind="device", id=device_id) if device_id else ANONYMOUS

    if not token:
        raise UnauthorizedError("Missing credential")

    if device_id:
        expected = settings.device_tokens.get(device_id)
        if expected and _equal(token, expected):
            return Principal(kind="device", id=device_id)

    api_key = settings.api_key.get_secret_value() if settings.api_key else ""
    if api_key and _equal(token, api_key):
        return Principal(kind="device" if device_id else "app", id=device_id or "mobile")

    # Fall back to a match against any device token even when no id was declared,
    # so firmware that only knows its secret can still connect.
    for known_id, known_token in settings.device_tokens.items():
        if _equal(token, known_token):
            return Principal(kind="device", id=known_id)

    raise UnauthorizedError("Invalid credential")
