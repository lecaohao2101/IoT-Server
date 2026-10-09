"""Google Cloud Speech-to-Text v2, streaming.

One bidirectional gRPC stream per utterance. Audio is pushed in from the WebSocket
reader task through a queue, while a second task drains recognition responses, so
neither side ever blocks the other.

Voice-activity events are requested from the service: when Google reports
``SPEECH_ACTIVITY_END`` we can close the turn without waiting for our own silence
timer, which typically saves a few hundred milliseconds per utterance.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator

from app.ai.base import SpeechRecognizer, SttStream, Transcript
from app.core.errors import ProviderError
from app.core.utils import truncate

log = logging.getLogger(__name__)

_SENTINEL = object()

#: Google STT v2 rejects any ``StreamingRecognizeRequest`` whose ``audio`` field is
#: larger than this (INVALID_ARGUMENT). Callers hand us whole buffers -- an HTTP
#: upload of a 2 s recording is 64000 bytes -- so the split belongs here, next to
#: the API whose limit it is.
MAX_CHUNK_BYTES = 25_600


class GoogleSttStream(SttStream):
    def __init__(
        self,
        client,
        recognizer: str,
        streaming_config,
        request_cls,
        queue_maxsize: int = 256,
        log_text: bool = True,
    ) -> None:
        self._client = client
        self._recognizer = recognizer
        self._streaming_config = streaming_config
        self._request_cls = request_cls
        self._audio: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
        self._started = False
        self._closed = False
        self._log_text = log_text
        self._sent_bytes = 0
        self._sent_chunks = 0
        self._dropped_chunks = 0

    async def push(self, pcm: bytes) -> None:
        if self._closed or not pcm:
            return
        for start in range(0, len(pcm), MAX_CHUNK_BYTES):
            self._offer(pcm[start : start + MAX_CHUNK_BYTES])

    def _offer(self, chunk: bytes) -> None:
        self._sent_bytes += len(chunk)
        self._sent_chunks += 1
        try:
            self._audio.put_nowait(chunk)
        except asyncio.QueueFull:
            # Recognition has fallen behind. Dropping the oldest frame keeps the
            # stream real-time; dropping the newest would stutter the transcript.
            self._dropped_chunks += 1
            with contextlib.suppress(asyncio.QueueEmpty):
                self._audio.get_nowait()
            self._audio.put_nowait(chunk)

    async def end_of_audio(self) -> None:
        if not self._closed:
            log.info(
                "google stt audio sent",
                extra={
                    "bytes": self._sent_bytes,
                    "chunks": self._sent_chunks,
                    "max_chunk_bytes": MAX_CHUNK_BYTES,
                    # Non-zero means recognition fell behind and audio was lost,
                    # which shows up later as a truncated transcript.
                    "dropped_chunks": self._dropped_chunks,
                },
            )
            await self._audio.put(_SENTINEL)

    async def _requests(self) -> AsyncIterator:
        yield self._request_cls(
            recognizer=self._recognizer, streaming_config=self._streaming_config
        )
        while True:
            chunk = await self._audio.get()
            if chunk is _SENTINEL:
                return
            yield self._request_cls(audio=chunk)

    async def results(self) -> AsyncIterator[Transcript]:
        self._started = True
        started = time.monotonic()
        try:
            responses = await self._client.streaming_recognize(requests=self._requests())
            async for response in responses:
                for item in self._decode(response):
                    if item.is_final and item.text:
                        log.info(
                            "google stt final",
                            extra={
                                "confidence": round(item.confidence, 3),
                                "after_ms": round((time.monotonic() - started) * 1000, 1),
                                "audio_bytes": self._sent_bytes,
                                **self._text_field(item.text),
                            },
                        )
                    elif item.text:
                        log.debug("google stt partial", extra=self._text_field(item.text))
                    yield item
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface one provider error type
            raise ProviderError(f"Google STT stream failed: {exc}") from exc

    def _text_field(self, text: str) -> dict:
        if self._log_text:
            return {"text": truncate(text, 200), "chars": len(text)}
        return {"chars": len(text)}

    @staticmethod
    def _decode(response) -> list[Transcript]:
        from google.cloud.speech_v2.types import cloud_speech

        out: list[Transcript] = []
        event = getattr(response, "speech_event_type", None)
        speech_ended = event == cloud_speech.StreamingRecognizeResponse.SpeechEventType.SPEECH_ACTIVITY_END

        for result in response.results:
            if not result.alternatives:
                continue
            best = result.alternatives[0]
            text = (best.transcript or "").strip()
            if not text:
                continue
            out.append(
                Transcript(
                    text=text,
                    is_final=bool(result.is_final),
                    confidence=float(best.confidence or 0.0),
                    speech_ended=speech_ended,
                )
            )
        if speech_ended and not out:
            out.append(Transcript(text="", is_final=False, speech_ended=True))
        return out

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._started:
            with contextlib.suppress(Exception):
                self._audio.put_nowait(_SENTINEL)


class GoogleRecognizer(SpeechRecognizer):
    name = "google-stt-v2"

    def __init__(
        self,
        *,
        project_id: str,
        location: str = "global",
        language: str = "vi-VN",
        alt_languages: tuple[str, ...] = (),
        model: str = "long",
        log_text: bool = True,
    ) -> None:
        if not project_id:
            raise ProviderError("GOOGLE_PROJECT_ID is required for Google STT")
        self._project_id = project_id
        self._location = location or "global"
        self._language = language
        self._alt_languages = tuple(alt_languages)
        self._model = model
        self._log_text = log_text
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            from google.api_core.client_options import ClientOptions
            from google.cloud.speech_v2 import SpeechAsyncClient

            options = None
            if self._location and self._location != "global":
                options = ClientOptions(api_endpoint=f"{self._location}-speech.googleapis.com")
            self._client = SpeechAsyncClient(client_options=options)
            log.info(
                "google stt client ready",
                extra={"project": self._project_id, "location": self._location},
            )
        return self._client

    def open_stream(self, *, language: str | None = None, sample_rate: int = 16000) -> SttStream:
        from google.cloud.speech_v2.types import cloud_speech

        client = self._ensure_client()
        recognizer = f"projects/{self._project_id}/locations/{self._location}/recognizers/_"

        config = cloud_speech.RecognitionConfig(
            explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=sample_rate,
                audio_channel_count=1,
            ),
            language_codes=[language or self._language, *self._alt_languages],
            model=self._model,
            features=cloud_speech.RecognitionFeatures(
                enable_automatic_punctuation=True,
                enable_word_time_offsets=False,
                max_alternatives=1,
            ),
        )
        streaming_config = cloud_speech.StreamingRecognitionConfig(
            config=config,
            streaming_features=cloud_speech.StreamingRecognitionFeatures(
                interim_results=True,
                enable_voice_activity_events=True,
            ),
        )
        log.info(
            "google stt stream opening",
            extra={
                "language": language or self._language,
                "alt_languages": list(self._alt_languages),
                "model": self._model,
                "sample_rate": sample_rate,
                "location": self._location,
            },
        )
        return GoogleSttStream(
            client=client,
            recognizer=recognizer,
            streaming_config=streaming_config,
            request_cls=cloud_speech.StreamingRecognizeRequest,
            log_text=self._log_text,
        )
