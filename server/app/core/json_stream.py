"""Incremental extraction of one string field from a JSON document that is still
being streamed by the LLM.

Why: the model answers with ``{"speech": "...", "commands": [...]}``. Waiting for
the closing brace before speaking would add a second of dead air, so we pull
``speech`` out character by character and hand it to TTS while ``commands`` is
still being generated.
"""

from __future__ import annotations

import json
import re
from typing import Any

_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class StreamingStringField:
    """Feed raw JSON text in; get decoded deltas of a single top-level string field."""

    def __init__(self, field: str) -> None:
        self._opener = re.compile('"' + re.escape(field) + r'"\s*:\s*"')
        self._buf = ""
        self._inside = False
        self._done = False
        self._parts: list[str] = []

    @property
    def done(self) -> bool:
        return self._done

    @property
    def value(self) -> str:
        return "".join(self._parts)

    def feed(self, chunk: str) -> str:
        """Return the newly decoded piece of the field (may be empty)."""
        if self._done or not chunk:
            return ""
        self._buf += chunk

        if not self._inside:
            match = self._opener.search(self._buf)
            if not match:
                # Keep a short tail: the key may straddle two chunks.
                self._buf = self._buf[-64:]
                return ""
            self._buf = self._buf[match.end() :]
            self._inside = True

        out: list[str] = []
        buf = self._buf
        n = len(buf)
        i = 0
        while i < n:
            ch = buf[i]
            if ch == '"':
                self._done = True
                i += 1
                break
            if ch == "\\":
                if i + 1 >= n:
                    break  # escape split across chunks -- wait for more input
                nxt = buf[i + 1]
                if nxt == "u":
                    if i + 6 > n:
                        break
                    try:
                        out.append(chr(int(buf[i + 2 : i + 6], 16)))
                    except ValueError:
                        out.append(buf[i : i + 6])
                    i += 6
                    continue
                out.append(_ESCAPES.get(nxt, nxt))
                i += 2
                continue
            out.append(ch)
            i += 1

        self._buf = buf[i:]
        delta = "".join(out)
        self._parts.append(delta)
        return delta


def strip_code_fence(text: str) -> str:
    return _FENCE_RE.sub("", text or "").strip()


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse the first balanced JSON object in ``text``, tolerating fences and prose."""
    cleaned = strip_code_fence(text)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start == -1:
        raise ValueError("no JSON object found in model output")

    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(cleaned)):
        ch = cleaned[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                parsed = json.loads(cleaned[start : idx + 1])
                if not isinstance(parsed, dict):
                    raise ValueError("model output is not a JSON object")
                return parsed
    raise ValueError("unterminated JSON object in model output")


class SentenceChunker:
    """Buffer streamed text and release it one speakable clause at a time.

    TTS latency is dominated by request count, not length, so we avoid synthesising
    two-word fragments: a clause is emitted at sentence punctuation, or once it has
    grown past ``soft_limit`` characters and reaches a comma.
    """

    _TERMINALS = ".!?…\n"
    _SOFT_BREAKS = ",;:"

    def __init__(self, soft_limit: int = 60, hard_limit: int = 180, min_len: int = 12) -> None:
        self.soft_limit = soft_limit
        self.hard_limit = hard_limit
        self.min_len = min_len
        self._buf = ""

    def feed(self, text: str) -> list[str]:
        self._buf += text
        chunks: list[str] = []
        while True:
            idx = self._find_break()
            if idx is None:
                break
            piece = self._buf[: idx + 1].strip()
            self._buf = self._buf[idx + 1 :]
            if piece:
                chunks.append(piece)
        return chunks

    def flush(self) -> str:
        piece = self._buf.strip()
        self._buf = ""
        return piece

    def _find_break(self) -> int | None:
        for i, ch in enumerate(self._buf):
            if ch in self._TERMINALS and i + 1 >= self.min_len:
                return i
            if ch in self._SOFT_BREAKS and i + 1 >= self.soft_limit:
                return i
            if i + 1 >= self.hard_limit and ch == " ":
                return i
        return None
