"""Google Cloud Text-to-Speech.

Synthesis happens per clause, not per reply: the orchestrator sends each finished
clause as soon as the model produces it, so the first audio reaches the speaker
while the model is still writing the second sentence.

LINEAR16 responses come back wrapped in a RIFF container. The ESP32 wants raw
frames for its I2S DAC, so the header is stripped here rather than on the device.
"""

from __future__ import annotations

import asyncio
import logging

from app.ai.base import AudioEncoding, SpeechAudio, SpeechSynthesizer
from app.core.audio import wav_to_pcm
from app.core.errors import ProviderError, ProviderTimeoutError

log = logging.getLogger(__name__)


class GoogleSynthesizer(SpeechSynthesizer):
    name = "google-tts"

    def __init__(
        self,
        *,
        voice: str = "vi-VN-Neural2-A",
        language: str = "vi-VN",
        speaking_rate: float = 1.0,
        pitch: float = 0.0,
        timeout_s: float = 10.0,
    ) -> None:
        self._voice = voice
        self._language = language
        self._speaking_rate = speaking_rate
        self._pitch = pitch
        self._timeout = timeout_s
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            from google.cloud import texttospeech_v1 as tts

            self._client = tts.TextToSpeechAsyncClient()
            log.info("google tts client ready", extra={"voice": self._voice})
        return self._client

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

        from google.cloud import texttospeech_v1 as tts

        client = self._ensure_client()
        wanted_voice = voice or self._voice
        audio_encoding = (
            tts.AudioEncoding.MP3 if encoding is AudioEncoding.MP3 else tts.AudioEncoding.LINEAR16
        )

        request = tts.SynthesizeSpeechRequest(
            input=tts.SynthesisInput(text=clean),
            voice=tts.VoiceSelectionParams(
                language_code=self._language_of(wanted_voice), name=wanted_voice
            ),
            audio_config=tts.AudioConfig(
                audio_encoding=audio_encoding,
                sample_rate_hertz=sample_rate,
                speaking_rate=self._speaking_rate,
                pitch=self._pitch,
            ),
        )

        try:
            async with asyncio.timeout(self._timeout):
                response = await client.synthesize_speech(request=request)
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                f"Google TTS timed out after {self._timeout:g}s"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Google TTS failed: {exc}") from exc

        payload = response.audio_content or b""
        if encoding is AudioEncoding.MP3:
            return SpeechAudio(audio=payload, encoding=AudioEncoding.MP3, sample_rate=sample_rate)

        pcm, fmt = wav_to_pcm(payload)
        return SpeechAudio(
            audio=pcm,
            encoding=AudioEncoding.PCM16,
            sample_rate=fmt.sample_rate if pcm else sample_rate,
        )

    def _language_of(self, voice_name: str) -> str:
        """``vi-VN-Neural2-A`` -> ``vi-VN``; fall back to the configured language."""
        parts = voice_name.split("-")
        return "-".join(parts[:2]) if len(parts) >= 2 else self._language
