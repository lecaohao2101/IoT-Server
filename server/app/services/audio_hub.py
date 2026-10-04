"""Audio Broadcast Hub: fan-out synthesised TTS audio to external speakers and WebSockets.

Allows any connected speaker (like an ESP32 Bluetooth A2DP gateway running
esp32_connect_loa.ino, or a browser/web client) to stream real-time synthesized speech
from Google TTS at its native sample rate and channel layout (e.g. 44.1kHz stereo).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.ai.base import AudioEncoding
from app.core.audio import convert_pcm
from app.services.orchestrator import TurnSink

log = logging.getLogger(__name__)


@dataclass(eq=False, slots=True)
class AudioSubscriber:
    queue: asyncio.Queue[bytes | None]
    target_rate: int = 44100
    target_channels: int = 2
    src_rate: int = 16000
    src_channels: int = 1


class AudioBroadcastHub:
    """Pub/sub fan-out for real-time PCM audio streams."""

    def __init__(self) -> None:
        self._subscribers: set[AudioSubscriber] = set()
        self._lock = asyncio.Lock()

    @property
    def active_subscribers_count(self) -> int:
        return len(self._subscribers)

    async def broadcast_chunk(
        self,
        pcm: bytes,
        src_rate: int = 16000,
        src_channels: int = 1,
    ) -> None:
        """Broadcast a PCM16 chunk to all registered speaker clients."""
        if not pcm:
            return

        async with self._lock:
            subscribers = list(self._subscribers)

        for sub in subscribers:
            try:
                # Convert from 16kHz mono to client's format (e.g. 44.1kHz stereo)
                converted = convert_pcm(
                    pcm,
                    src_rate=src_rate,
                    dst_rate=sub.target_rate,
                    src_channels=src_channels,
                    dst_channels=sub.target_channels,
                )
                sub.queue.put_nowait(converted)
            except asyncio.QueueFull:
                log.warning("Audio subscriber queue full, dropping frame")
            except Exception as exc:  # noqa: BLE001
                log.debug("Error feeding audio subscriber", extra={"error": str(exc)})

    async def broadcast_utterance(
        self,
        pcm: bytes,
        src_rate: int = 16000,
        src_channels: int = 1,
        chunk_ms: int = 100,
    ) -> None:
        """Broadcast an entire PCM audio buffer in paced chunks to simulate streaming."""
        if not pcm:
            return
        bytes_per_sec = src_rate * src_channels * 2
        chunk_bytes = int(bytes_per_sec * (chunk_ms / 1000.0))
        chunk_bytes -= chunk_bytes % (src_channels * 2)

        for offset in range(0, len(pcm), max(1, chunk_bytes)):
            chunk = pcm[offset : offset + max(1, chunk_bytes)]
            await self.broadcast_chunk(chunk, src_rate=src_rate, src_channels=src_channels)
            await asyncio.sleep(chunk_ms / 1000.0 * 0.9)

    async def subscribe(
        self,
        target_rate: int = 44100,
        target_channels: int = 2,
        queue_maxsize: int = 100,
    ) -> AsyncIterator[bytes]:
        """Subscribe to live speech audio. Automatically converts incoming PCM to target layout."""
        sub = AudioSubscriber(
            queue=asyncio.Queue(maxsize=queue_maxsize),
            target_rate=target_rate,
            target_channels=target_channels,
        )
        async with self._lock:
            self._subscribers.add(sub)
            log.info(
                "Audio subscriber joined",
                extra={
                    "target_rate": target_rate,
                    "target_channels": target_channels,
                    "total": len(self._subscribers),
                },
            )

        try:
            while True:
                chunk = await sub.queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            async with self._lock:
                self._subscribers.discard(sub)
                log.info("Audio subscriber left", extra={"remaining": len(self._subscribers)})


class HubTurnSink(TurnSink):
    """A TurnSink that pipes all synthesised assistant speech into the AudioBroadcastHub."""

    def __init__(self, hub: AudioBroadcastHub, passthrough: TurnSink | None = None) -> None:
        self._hub = hub
        self._passthrough = passthrough
        self._sample_rate = 16000

    async def assistant_delta(self, text: str) -> None:
        if self._passthrough:
            await self._passthrough.assistant_delta(text)

    async def audio_start(self, encoding: AudioEncoding, sample_rate: int) -> None:
        self._sample_rate = sample_rate
        if self._passthrough:
            await self._passthrough.audio_start(encoding, sample_rate)

    async def audio_chunk(self, payload: bytes) -> None:
        if payload:
            await self._hub.broadcast_chunk(payload, src_rate=self._sample_rate, src_channels=1)
        if self._passthrough:
            await self._passthrough.audio_chunk(payload)

    async def audio_end(self) -> None:
        if self._passthrough:
            await self._passthrough.audio_end()

    async def notice(self, code: str, message: str) -> None:
        if self._passthrough:
            await self._passthrough.notice(code, message)

    @property
    def cancelled(self) -> bool:
        return self._passthrough.cancelled if self._passthrough else False
