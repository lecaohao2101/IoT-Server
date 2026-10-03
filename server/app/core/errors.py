"""Domain error hierarchy. Every error carries a stable machine-readable code."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class: `code` is part of the API contract, `message` is for humans."""

    code = "internal_error"
    http_status = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out


class ConfigError(AppError):
    code = "config_error"


class NotFoundError(AppError):
    code = "not_found"
    http_status = 404


class ValidationError(AppError):
    code = "validation_error"
    http_status = 422


class UnauthorizedError(AppError):
    code = "unauthorized"
    http_status = 401


class RateLimitedError(AppError):
    code = "rate_limited"
    http_status = 429


class UnsafeCommandError(AppError):
    code = "unsafe_command"
    http_status = 422


class ProviderError(AppError):
    """An upstream AI provider failed (STT/TTS/LLM)."""

    code = "provider_error"
    http_status = 502


class ProviderTimeoutError(ProviderError):
    code = "provider_timeout"
    http_status = 504


class TransportError(AppError):
    """MQTT / downstream delivery failure."""

    code = "transport_error"
    http_status = 503
