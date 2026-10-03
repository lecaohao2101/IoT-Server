"""Provider-neutral interfaces for the three AI stages.

The orchestrator only ever sees these types. Swapping Google STT for a self-hosted
Whisper, or Gemini for another model, is a change to one factory function.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ------------------------------------------------------------------- speech-to-text


@dataclass(slots=True)
class Transcript:
    """One recognition result. Interim results arrive many times per utterance."""

    text: str
    is_final: bool = False
    confidence: float = 0.0
    #: Provider-signalled end of speech, when it detects one before we do.
    speech_ended: bool = False


class SttStream(abc.ABC):
    """A single utterance's recognition stream.

    Lifecycle: ``push()`` audio repeatedly, ``end_of_audio()`` once, consume
    ``results()`` throughout, then ``aclose()``.
    """

    @abc.abstractmethod
    async def push(self, pcm: bytes) -> None: ...

    @abc.abstractmethod
    async def end_of_audio(self) -> None: ...

    @abc.abstractmethod
    def results(self) -> AsyncIterator[Transcript]: ...

    @abc.abstractmethod
    async def aclose(self) -> None: ...

    async def __aenter__(self) -> SttStream:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()


class SpeechRecognizer(abc.ABC):
    name: str = "stt"

    @abc.abstractmethod
    def open_stream(self, *, language: str | None = None, sample_rate: int = 16000) -> SttStream:
        """Begin recognising one utterance. Cheap: no I/O until the first push."""

    async def aclose(self) -> None:  # pragma: no cover - most providers are stateless
        return None


# ------------------------------------------------------------------- text-to-speech


class AudioEncoding(str, Enum):
    PCM16 = "pcm16"  # headerless little-endian, what the ESP32 wants
    MP3 = "mp3"  # what a phone prefers over a mobile link
    WAV = "wav"


@dataclass(slots=True)
class SpeechAudio:
    audio: bytes
    encoding: AudioEncoding
    sample_rate: int

    @property
    def is_empty(self) -> bool:
        return not self.audio


class SpeechSynthesizer(abc.ABC):
    name: str = "tts"

    @abc.abstractmethod
    async def synthesize(
        self,
        text: str,
        *,
        encoding: AudioEncoding = AudioEncoding.PCM16,
        sample_rate: int = 16000,
        voice: str | None = None,
    ) -> SpeechAudio: ...

    async def aclose(self) -> None:  # pragma: no cover
        return None


# ------------------------------------------------------------------- reasoning


@dataclass(slots=True)
class LlmMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass(slots=True)
class LlmUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class LlmResult:
    text: str
    usage: LlmUsage = field(default_factory=LlmUsage)
    model: str = ""


class LanguageModel(abc.ABC):
    name: str = "llm"

    @abc.abstractmethod
    def stream(
        self,
        *,
        system: str,
        messages: list[LlmMessage],
        json_schema: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """Yield text deltas. Implementations must not raise after the first yield
        without the orchestrator being able to fall back on what it already has."""

    async def complete(
        self,
        *,
        system: str,
        messages: list[LlmMessage],
        json_schema: dict[str, Any] | None = None,
    ) -> LlmResult:
        parts: list[str] = []
        async for delta in self.stream(system=system, messages=messages, json_schema=json_schema):
            parts.append(delta)
        return LlmResult(text="".join(parts), model=self.name)

    async def aclose(self) -> None:  # pragma: no cover
        return None
