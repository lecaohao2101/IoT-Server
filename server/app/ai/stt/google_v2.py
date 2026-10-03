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
from collections.abc import AsyncIterator

from app.ai.base import SpeechRecognizer, SttStream, Transcript
from app.core.errors import ProviderError

log = logging.getLogger(__name__)

_SENTINEL = object()


class GoogleSttStream(SttStream):
    def __init__(
        self,
        client,
        recognizer: str,
        streaming_config,
        request_cls,
        queue_maxsize: int = 256,
    ) -> None:
        self._client = client
        self._recognizer = recognizer
        self._streaming_config = streaming_config
        self._request_cls = request_cls
        self._audio: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
        self._started = False
        self._closed = False

    async def push(self, pcm: bytes) -> None:
        if self._closed or not pcm:
            return
        try:
            self._audio.put_nowait(pcm)
        except asyncio.QueueFull:
            # Recognition has fallen behind. Dropping the oldest frame keeps the
            # stream real-time; dropping the newest would stutter the transcript.
            with contextlib.suppress(asyncio.QueueEmpty):
                self._audio.get_nowait()
            self._audio.put_nowait(pcm)

    async def end_of_audio(self) -> None:
        if not self._closed:
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
        try:
            responses = await self._client.streaming_recognize(requests=self._requests())
            async for response in responses:
                for item in self._decode(response):
                    yield item
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface one provider error type
            raise ProviderError(f"Google STT stream failed: {exc}") from exc

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
    ) -> None:
        if not project_id:
            raise ProviderError("GOOGLE_PROJECT_ID is required for Google STT")
        self._project_id = project_id
        self._location = location or "global"
        self._language = language
        self._alt_languages = tuple(alt_languages)
        self._model = model
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
        return GoogleSttStream(
            client=client,
            recognizer=recognizer,
            streaming_config=streaming_config,
            request_cls=cloud_speech.StreamingRecognizeRequest,
        )
