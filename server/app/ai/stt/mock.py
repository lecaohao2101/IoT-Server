"""Offline speech recogniser.

Two behaviours, both deterministic:

* If the pushed bytes decode as printable UTF-8 text, that text *is* the
  transcript. The ESP32 simulator and the WebSocket tests use this to drive the
  full pipeline without recording anything.
* Otherwise it walks a canned list of Vietnamese utterances, so a demo with real
  microphone audio still produces sensible turns with no cloud credentials.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.ai.base import SpeechRecognizer, SttStream, Transcript

DEFAULT_SCRIPT = (
    "bật đèn phòng khách",
    "giảm độ sáng xuống 40 phần trăm",
    "nhiệt độ phòng ngủ bao nhiêu",
    "mở rèm phòng khách",
    "tắt hết đèn trong nhà",
)


def _as_text(payload: bytes) -> str | None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    # Raw PCM decodes as UTF-8 surprisingly often; require it to look like prose.
    printable = sum(1 for ch in stripped if ch.isprintable())
    return stripped if printable / len(stripped) > 0.95 else None


class MockSttStream(SttStream):
    def __init__(self, fallback: str, interim_delay_s: float = 0.0) -> None:
        self._fallback = fallback
        self._interim_delay = interim_delay_s
        self._text_parts: list[str] = []
        self._queue: asyncio.Queue[Transcript | None] = asyncio.Queue()
        self._closed = False
        self._emitted_interim = False

    async def push(self, pcm: bytes) -> None:
        if self._closed:
            return
        text = _as_text(pcm)
        if text:
            self._text_parts.append(text)
            if self._interim_delay:
                await asyncio.sleep(self._interim_delay)
            await self._queue.put(Transcript(text=" ".join(self._text_parts), is_final=False))
            self._emitted_interim = True
        elif not self._emitted_interim:
            await self._queue.put(Transcript(text=self._fallback, is_final=False))
            self._emitted_interim = True

    async def end_of_audio(self) -> None:
        if self._closed:
            return
        final = " ".join(self._text_parts).strip() or self._fallback
        await self._queue.put(Transcript(text=final, is_final=True, confidence=0.95))
        await self._queue.put(None)

    async def results(self) -> AsyncIterator[Transcript]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            await self._queue.put(None)


class MockRecognizer(SpeechRecognizer):
    name = "mock-stt"

    def __init__(self, script: tuple[str, ...] = DEFAULT_SCRIPT) -> None:
        self._script = script or DEFAULT_SCRIPT
        self._cursor = 0

    def open_stream(self, *, language: str | None = None, sample_rate: int = 16000) -> SttStream:
        fallback = self._script[self._cursor % len(self._script)]
        self._cursor += 1
        return MockSttStream(fallback)
