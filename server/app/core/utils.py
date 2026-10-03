"""Small shared helpers: ids, clocks, timers, text folding."""

from __future__ import annotations

import re
import time
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}_{raw}" if prefix else raw


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_iso() -> str:
    return utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


def monotonic_ms() -> float:
    return time.monotonic() * 1000.0


@contextmanager
def stopwatch() -> Iterator[list[float]]:
    """`with stopwatch() as t:` then read `t[0]` afterwards for elapsed ms."""
    holder = [0.0]
    start = time.monotonic()
    try:
        yield holder
    finally:
        holder[0] = (time.monotonic() - start) * 1000.0


_WS_RE = re.compile(r"\s+")


def fold(text: str) -> str:
    """Normalise Vietnamese text for alias matching: NFC, lowercase, single spaces."""
    text = unicodedata.normalize("NFC", text or "").strip().lower()
    return _WS_RE.sub(" ", text)


def strip_accents(text: str) -> str:
    """Drop diacritics so `den phong khach` matches `đèn phòng khách`."""
    text = unicodedata.normalize("NFD", fold(text))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.replace("đ", "d").replace("Đ", "d")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def truncate(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
