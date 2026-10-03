"""Audio plumbing: WAV container handling, framing and a cheap energy endpointer.

Everything on the wire is 16 kHz, mono, signed 16-bit little-endian PCM -- the one
format an ESP32 I2S microphone and Google's streaming recognizer agree on. The WAV
helpers exist because Google TTS returns LINEAR16 wrapped in a RIFF container while
the firmware wants headerless frames it can push straight into the I2S DAC.

Implemented without ``audioop``: that module was removed from the standard library
in Python 3.13, and the three operations we need are a few lines each.
"""

from __future__ import annotations

import array
import math
import struct
import sys
from collections.abc import Iterator
from dataclasses import dataclass

RIFF = b"RIFF"
WAVE = b"WAVE"
_LITTLE_ENDIAN = sys.byteorder == "little"


@dataclass(frozen=True, slots=True)
class AudioFormat:
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2  # bytes

    @property
    def bytes_per_second(self) -> int:
        return self.sample_rate * self.channels * self.sample_width

    @property
    def frame_size(self) -> int:
        return self.channels * self.sample_width

    def bytes_for_ms(self, ms: float) -> int:
        raw = int(self.bytes_per_second * ms / 1000.0)
        return raw - (raw % self.frame_size)

    def duration_ms(self, payload: bytes) -> float:
        return len(payload) * 1000.0 / self.bytes_per_second


DEFAULT_FORMAT = AudioFormat()


# --------------------------------------------------------------------- PCM16


def _as_samples(pcm: bytes) -> array.array:
    """View little-endian PCM16 bytes as signed shorts, dropping a partial sample."""
    usable = len(pcm) - (len(pcm) % 2)
    samples = array.array("h")
    samples.frombytes(pcm[:usable])
    if not _LITTLE_ENDIAN:  # pragma: no cover - x86/ARM are little-endian
        samples.byteswap()
    return samples


def _as_bytes(samples: array.array) -> bytes:
    if not _LITTLE_ENDIAN:  # pragma: no cover
        samples = array.array("h", samples)
        samples.byteswap()
    return samples.tobytes()


def rms(pcm: bytes, sample_width: int = 2) -> int:
    """Root-mean-square amplitude of a PCM16 buffer; 0 when there is nothing to measure."""
    if sample_width != 2:
        raise ValueError("only 16-bit PCM is supported")
    samples = _as_samples(pcm)
    if not samples:
        return 0
    total = 0
    for s in samples:
        total += s * s
    return int(math.sqrt(total / len(samples)))


def resample(pcm: bytes, src_rate: int, dst_rate: int, channels: int = 1) -> bytes:
    """Linear-interpolation rate conversion for PCM16. Used when a phone records at 48 kHz."""
    if src_rate == dst_rate or not pcm or src_rate <= 0 or dst_rate <= 0:
        return pcm
    src = _as_samples(pcm)
    if channels > 1:
        src = _as_samples(to_mono(pcm, channels))
    n_src = len(src)
    if n_src < 2:
        return _as_bytes(src)

    n_dst = max(1, int(n_src * dst_rate / src_rate))
    step = (n_src - 1) / max(1, n_dst - 1) if n_dst > 1 else 0.0
    out = array.array("h", bytes(2 * n_dst))
    for i in range(n_dst):
        pos = i * step
        left = int(pos)
        right = min(left + 1, n_src - 1)
        frac = pos - left
        out[i] = int(src[left] + (src[right] - src[left]) * frac)
    return _as_bytes(out)


def to_mono(pcm: bytes, channels: int) -> bytes:
    """Average interleaved channels down to one."""
    if channels <= 1:
        return pcm
    src = _as_samples(pcm)
    n = len(src) // channels
    out = array.array("h", bytes(2 * n))
    for i in range(n):
        base = i * channels
        out[i] = sum(src[base : base + channels]) // channels
    return _as_bytes(out)


# ----------------------------------------------------------------- container


def is_wav(payload: bytes) -> bool:
    return len(payload) >= 12 and payload[:4] == RIFF and payload[8:12] == WAVE


def wav_to_pcm(payload: bytes) -> tuple[bytes, AudioFormat]:
    """Unwrap a RIFF/WAVE container. Returns the payload unchanged if it is raw PCM."""
    if not is_wav(payload):
        return payload, DEFAULT_FORMAT

    pos = 12
    fmt = DEFAULT_FORMAT
    end = len(payload)
    while pos + 8 <= end:
        chunk_id = payload[pos : pos + 4]
        (size,) = struct.unpack_from("<I", payload, pos + 4)
        body = pos + 8
        if chunk_id == b"fmt " and size >= 16:
            channels, rate, _byte_rate, _align, bits = struct.unpack_from(
                "<HIIHH", payload, body + 2
            )
            fmt = AudioFormat(
                sample_rate=rate, channels=channels, sample_width=max(1, bits // 8)
            )
        elif chunk_id == b"data":
            return payload[body : body + size], fmt
        pos = body + size + (size & 1)  # RIFF chunks are word-aligned
    return b"", fmt


def pcm_to_wav(pcm: bytes, fmt: AudioFormat = DEFAULT_FORMAT) -> bytes:
    """Wrap raw PCM so a browser ``<audio>`` element or a test fixture can play it."""
    block_align = fmt.channels * fmt.sample_width
    header = b"".join(
        (
            RIFF,
            struct.pack("<I", 36 + len(pcm)),
            WAVE,
            b"fmt ",
            struct.pack(
                "<IHHIIHH",
                16,
                1,
                fmt.channels,
                fmt.sample_rate,
                fmt.bytes_per_second,
                block_align,
                fmt.sample_width * 8,
            ),
            b"data",
            struct.pack("<I", len(pcm)),
        )
    )
    return header + pcm


def iter_frames(payload: bytes, frame_bytes: int) -> Iterator[bytes]:
    """Split a buffer into fixed-size frames; the tail may be short."""
    if frame_bytes <= 0:
        yield payload
        return
    for start in range(0, len(payload), frame_bytes):
        yield payload[start : start + frame_bytes]


# ---------------------------------------------------------------- endpointer


class SilenceEndpointer:
    """Energy-based end-of-utterance detector.

    A fallback for transports whose recognizer gives us no voice-activity events
    (and for the mock provider). It adapts to the room: the noise estimate tracks
    the quietest frames seen so far, and speech must exceed it by a margin, so a
    humming air-conditioner does not hold the microphone open forever.
    """

    def __init__(
        self,
        fmt: AudioFormat = DEFAULT_FORMAT,
        silence_ms: int = 900,
        min_speech_ms: int = 300,
        start_threshold: int = 500,
        margin: float = 2.5,
    ) -> None:
        self.fmt = fmt
        self.silence_ms = silence_ms
        self.min_speech_ms = min_speech_ms
        self.margin = margin
        self._noise = float(start_threshold)
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._triggered = False

    @property
    def speech_detected(self) -> bool:
        return self._triggered

    @property
    def speech_ms(self) -> float:
        return self._speech_ms

    def reset(self) -> None:
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._triggered = False

    def accept(self, frame: bytes) -> bool:
        """Feed one frame. Returns True when the utterance looks finished."""
        duration = self.fmt.duration_ms(frame)
        if duration <= 0:
            return False
        level = rms(frame, self.fmt.sample_width)

        if level > self._noise * self.margin:
            self._triggered = True
            self._speech_ms += duration
            self._silence_ms = 0.0
        else:
            self._noise = 0.95 * self._noise + 0.05 * max(level, 1)
            if self._triggered:
                self._silence_ms += duration

        return (
            self._triggered
            and self._speech_ms >= self.min_speech_ms
            and self._silence_ms >= self.silence_ms
        )
