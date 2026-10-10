"""An in-memory tail of recent log records, for the browser log viewer.

Fly's log console flattens a record to one line, which loses exactly the part
that matters here: the structured fields carried on ``extra`` -- ``peak_level``,
``audio_bytes``, ``confidence``, ``tts_audio_bytes`` and friends. This keeps the
last few hundred records whole, so ``/logs`` can show the fields next to the
message and filter the routine traffic out of the way.

The buffer is per-process and deliberately small; it is a live tail, not storage.
Anything that must survive a restart still belongs in the real log stream.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any

from app.logging_setup import _RESERVED, trace_id_var

_JSON_SAFE = (str, int, float, bool, type(None))

#: Trang /logs hỏi server mỗi giây. Nếu để nguyên, mỗi lần hỏi lại sinh thêm hai
#: bản ghi và vòng đệm bị chính nó lấp đầy sau vài phút -- người dùng mở lên chỉ
#: thấy tiếng vọng của mình. Không giữ lưu lượng đó.
_SELF_PATH = "/api/v1/logs"


def _plain(value: Any) -> Any:
    """Coerce a field to something JSON can carry, without losing the detail."""
    if isinstance(value, _JSON_SAFE):
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return str(value)


class LogTail(logging.Handler):
    """Keeps the most recent records as dictionaries, newest last."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self._records: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._seq = 0

    def emit(self, record: logging.LogRecord) -> None:
        if self._is_self_traffic(record):
            return
        try:
            payload = self._to_payload(record)
        except Exception:  # noqa: BLE001 - a broken field must never break logging
            return
        with self._lock:
            self._seq += 1
            payload["seq"] = self._seq
            self._records.append(payload)

    @staticmethod
    def _is_self_traffic(record: logging.LogRecord) -> bool:
        if getattr(record, "path", None) == _SELF_PATH:
            return True
        return record.name == "uvicorn.access" and _SELF_PATH in str(record.args or ())

    def _to_payload(self, record: logging.LogRecord) -> dict[str, Any]:
        fields = {
            key: _plain(value)
            for key, value in record.__dict__.items()
            if key not in _RESERVED and not key.startswith("_")
        }
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": trace_id_var.get(),
            "fields": fields,
        }
        if record.exc_info:
            payload["exc"] = logging.Formatter().formatException(record.exc_info)
        return payload

    def since(self, after: int = 0, limit: int = 500) -> tuple[list[dict[str, Any]], int]:
        """Records newer than ``after``, plus the latest sequence number.

        ``after`` ahead of our newest sequence means the process restarted and the
        client is holding a stale cursor; send the whole buffer rather than
        nothing, so the page refills instead of looking frozen.
        """
        with self._lock:
            newest = self._seq
            snapshot = list(self._records)
        if after > newest:
            after = 0
        fresh = [r for r in snapshot if r["seq"] > after]
        return fresh[-limit:], newest

    def resize(self, capacity: int) -> None:
        """Change how many records are kept. ``deque`` fixes maxlen at build time,
        so this rebuilds it, keeping the newest records that still fit."""
        capacity = max(50, capacity)
        with self._lock:
            if self._records.maxlen == capacity:
                return
            self._records = deque(self._records, maxlen=capacity)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


#: Installed by ``setup_logging``; the route reads it directly.
log_tail = LogTail()
