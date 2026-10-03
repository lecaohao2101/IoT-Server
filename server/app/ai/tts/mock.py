"""Offline speech synthesiser.

Produces a short, quiet tone whose length tracks the text, so timing, framing and
barge-in behave the same as with real audio. Deterministic: the same text always
yields the same bytes, which keeps WebSocket tests stable.
"""

from __future__ import annotations

import array
import math
import zlib

from app.ai.base import AudioEncoding, SpeechAudio, SpeechSynthesizer
from app.core.audio import AudioFormat, pcm_to_wav

_SPEAKING_RATE_CPS = 15.0  # characters per second of synthetic speech
_MIN_MS = 250.0
_MAX_MS = 8000.0


class MockSynthesizer(SpeechSynthesizer):
    name = "mock-tts"

    def __init__(self, amplitude: int = 2500) -> None:
        self._amplitude = amplitude

    async def synthesize(
        self,
        text: str,
        *,
        encoding: AudioEncoding = AudioEncoding.PCM16,
        sample_rate: int = 16000,
        voice: str | None = None,
    ) -> SpeechAudio:
        clean = (text or "").strip()
        if not clean:
            return SpeechAudio(audio=b"", encoding=encoding, sample_rate=sample_rate)

        duration_ms = min(_MAX_MS, max(_MIN_MS, len(clean) / _SPEAKING_RATE_CPS * 1000.0))
        # Derive the pitch from the text so different replies sound different.
        freq = 180.0 + (zlib.crc32(clean.encode("utf-8")) % 120)
        pcm = self._tone(freq, duration_ms, sample_rate)

        if encoding is AudioEncoding.WAV:
            return SpeechAudio(
                audio=pcm_to_wav(pcm, AudioFormat(sample_rate=sample_rate)),
                encoding=AudioEncoding.WAV,
                sample_rate=sample_rate,
            )
        # No MP3 encoder offline: hand back PCM and say so honestly.
        return SpeechAudio(audio=pcm, encoding=AudioEncoding.PCM16, sample_rate=sample_rate)

    def _tone(self, freq: float, duration_ms: float, sample_rate: int) -> bytes:
        count = int(sample_rate * duration_ms / 1000.0)
        fade = max(1, int(sample_rate * 0.01))  # 10 ms ramps, no clicks
        samples = array.array("h", bytes(2 * count))
        step = 2.0 * math.pi * freq / sample_rate
        for i in range(count):
            envelope = min(1.0, i / fade, (count - i) / fade)
            samples[i] = int(self._amplitude * envelope * math.sin(step * i))
        return samples.tobytes()
