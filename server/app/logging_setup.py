"""Structured logging with a request/session correlation id."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from collections.abc import Mapping
from typing import Any

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")

_RESERVED = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    | {"message", "asctime", "taskName"}
)


def safe_extra(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Prefix keys that ``logging`` reserves, so ``extra=`` can never raise."""
    return {(f"x_{k}" if k in _RESERVED else k): v for k, v in fields.items()}


_guard_installed = False


def _install_extra_guard() -> None:
    """Make an ``extra=`` key collision survivable for every logger in the process.

    ``Logger.makeRecord`` raises ``KeyError`` when an extra key shadows a
    ``LogRecord`` attribute -- ``message``, ``name``, ``args`` and friends. Raised
    from an exception handler that was about to return a tidy 502, it escapes as a
    500 with a full traceback: a logging detail turning into an API failure. Rename
    the offending keys instead.
    """
    global _guard_installed
    if _guard_installed:
        return
    original = logging.Logger.makeRecord

    def make_record(self, name, level, fn, lno, msg, args, exc_info, func=None, extra=None, sinfo=None):  # noqa: ANN001, ANN202, PLR0913
        if extra:
            extra = safe_extra(extra)
        return original(self, name, level, fn, lno, msg, args, exc_info, func, extra, sinfo)

    logging.Logger.makeRecord = make_record
    _guard_installed = True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": trace_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _RESERVED and not k.startswith("_")
        }
        suffix = (" " + " ".join(f"{k}={v}" for k, v in extras.items())) if extras else ""
        base = (
            f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} "
            f"[{trace_id_var.get()}] {record.name}: {record.getMessage()}{suffix}"
        )
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging(level: str = "INFO", as_json: bool = True) -> None:
    _install_extra_guard()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if as_json else HumanFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for noisy in ("uvicorn.access", "websockets", "aiomqtt", "httpx", "google"):
        logging.getLogger(noisy).setLevel(max(logging.INFO, root.level))
    logging.getLogger("uvicorn.error").handlers = [handler]
    logging.getLogger("uvicorn.access").handlers = [handler]
