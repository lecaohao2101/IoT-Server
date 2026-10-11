"""Acoustic Voice Biometrics and Speaker Verification.

Provides pure-Python acoustic fingerprinting (pitch autocorrelation, Mel-scale
filterbank, MFCC cepstral analysis, and spectral envelope) with zero external
C/PyTorch dependencies to stay within lean container limits while enabling
speaker enrollment and authorization.
"""

from __future__ import annotations

import cmath
import json
import logging
import math
import struct
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

FRAME_SIZE = 512  # 32 ms at 16 kHz
HOP_SIZE = 256  # 16 ms at 16 kHz
SAMPLE_RATE = 16000
MIN_VOICED_FRAMES = 3


def _hz_to_mel(f: float) -> float:
    return 2595.0 * math.log10(1.0 + f / 700.0)


def _mel_to_hz(m: float) -> float:
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def _fft(x: list[complex]) -> list[complex]:
    """Cooley-Tukey Radix-2 FFT in pure Python."""
    n = len(x)
    if n <= 1:
        return x
    even = _fft(x[0::2])
    odd = _fft(x[1::2])
    t = [cmath.exp(-2j * math.pi * k / n) * odd[k] for k in range(n // 2)]
    return [even[k] + t[k] for k in range(n // 2)] + [even[k] - t[k] for k in range(n // 2)]


def _l2_norm(v: list[float]) -> list[float]:
    mag = math.sqrt(sum(x * x for x in v))
    return [x / mag for x in v] if mag > 1e-6 else v


_HANN = [
    0.5 * (1.0 - math.cos(2.0 * math.pi * n / (FRAME_SIZE - 1))) for n in range(FRAME_SIZE)
]

# 16 Mel-scale filters spanning 80 Hz to 4000 Hz
_M_MIN = _hz_to_mel(80.0)
_M_MAX = _hz_to_mel(4000.0)
_MEL_POINTS = [_M_MIN + i * (_M_MAX - _M_MIN) / 17.0 for i in range(18)]
_BIN_POINTS = [int(round(_mel_to_hz(m) * FRAME_SIZE / SAMPLE_RATE)) for m in _MEL_POINTS]


class AcousticFingerprint:
    """Extracts a normalized 32-dimensional acoustic embedding vector from PCM16 audio."""

    @classmethod
    def extract(cls, pcm_bytes: bytes, sample_rate: int = SAMPLE_RATE) -> list[float] | None:
        """Extract a unit-norm embedding vector from 16-bit mono PCM bytes."""
        if len(pcm_bytes) < FRAME_SIZE * 2:
            return None

        # Unpack PCM16 to float in [-1.0, 1.0]
        num_samples = len(pcm_bytes) // 2
        try:
            samples = [
                s / 32768.0 for s in struct.unpack(f"<{num_samples}h", pcm_bytes[: num_samples * 2])
            ]
        except struct.error:
            return None

        num_frames = (len(samples) - FRAME_SIZE) // HOP_SIZE
        if num_frames < MIN_VOICED_FRAMES:
            return None

        min_lag = max(1, int(sample_rate / 400.0))  # 40 samples
        max_lag = min(int(sample_rate / 70.0), FRAME_SIZE - 1)  # 228 samples

        voiced_mel: list[list[float]] = []
        centroids: list[float] = []
        pitches: list[float] = []

        for i in range(num_frames):
            start = i * HOP_SIZE
            frame = samples[start : start + FRAME_SIZE]

            # RMS energy gating
            rms = math.sqrt(sum(s * s for s in frame) / FRAME_SIZE)
            if rms < 0.012:
                continue

            # Pitch estimation via autocorrelation
            r0 = sum(s * s for s in frame) + 1e-9
            best_lag = min_lag
            best_corr = -1.0
            for lag in range(min_lag, max_lag):
                corr = sum(frame[j] * frame[j + lag] for j in range(FRAME_SIZE - lag))
                if corr > best_corr:
                    best_corr = corr
                    best_lag = lag
            if best_corr / r0 > 0.35:
                pitches.append(sample_rate / best_lag)

            # Windowed FFT
            w_frame = [complex(frame[j] * _HANN[j], 0.0) for j in range(FRAME_SIZE)]
            freq_domain = _fft(w_frame)
            power = [
                (freq_domain[k].real ** 2 + freq_domain[k].imag ** 2) / FRAME_SIZE
                for k in range(FRAME_SIZE // 2)
            ]

            # Spectral Centroid
            tot_p = sum(power) + 1e-9
            centroid = sum(k * power[k] for k in range(len(power))) / (tot_p * len(power))
            centroids.append(centroid)

            # Mel filterbank
            fb_energies: list[float] = []
            for m in range(16):
                b_left, b_center, b_right = (
                    _BIN_POINTS[m],
                    _BIN_POINTS[m + 1],
                    _BIN_POINTS[m + 2],
                )
                if b_center <= b_left:
                    b_center = b_left + 1
                if b_right <= b_center:
                    b_right = b_center + 1
                e = 0.0
                for k in range(b_left, b_center):
                    if k < len(power):
                        e += power[k] * (k - b_left) / (b_center - b_left)
                for k in range(b_center, b_right):
                    if k < len(power):
                        e += power[k] * (b_right - k) / (b_right - b_center)
                fb_energies.append(math.log(1.0 + e * 100.0))
            voiced_mel.append(fb_energies)

        if len(voiced_mel) < MIN_VOICED_FRAMES:
            return None

        # Filterbank Mean & Std
        n_v = len(voiced_mel)
        raw_mean = [sum(voiced_mel[f][m] for f in range(n_v)) / n_v for m in range(16)]
        overall_mean = sum(raw_mean) / 16.0
        mean_mel = [m - overall_mean for m in raw_mean]

        std_mel = [
            math.sqrt(sum((voiced_mel[f][m] - raw_mean[m]) ** 2 for f in range(n_v)) / n_v)
            for m in range(16)
        ]
        mean_std = sum(std_mel) / 16.0
        std_mel = [s - mean_std for s in std_mel]

        # MFCC DCT Type-II with cepstral liftering (Coefficients 1..12)
        mfcc: list[float] = []
        for k in range(1, 13):
            c = sum(mean_mel[m] * math.cos(math.pi * k * (m + 0.5) / 16.0) for m in range(16))
            lifter = 1.0 + 6.0 * math.sin(math.pi * k / 12.0)
            mfcc.append(c * lifter)

        pitch_val = sum(pitches) / len(pitches) if pitches else 0.0
        pb = (
            [
                max(0.0, 1.0 - abs(pitch_val - 130.0) / 60.0),
                max(0.0, 1.0 - abs(pitch_val - 190.0) / 50.0),
                max(0.0, 1.0 - abs(pitch_val - 250.0) / 50.0),
                max(0.0, 1.0 - abs(pitch_val - 340.0) / 60.0),
            ]
            if pitch_val > 0
            else [0.0, 0.0, 0.0, 0.0]
        )

        norm_mfcc = _l2_norm(mfcc)
        norm_std = _l2_norm(std_mel)
        norm_pb = _l2_norm(pb)

        # Balanced fusion: MFCC (formants), Std Mel (dynamics), Pitch (vocal cord)
        comb = [0.4 * x for x in norm_mfcc] + [0.2 * x for x in norm_std] + [0.4 * x for x in norm_pb]
        unit_vec = _l2_norm(comb)
        return [round(v, 6) for v in unit_vec]


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute dot product of two unit-normalized vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    return sum(a * b for a, b in zip(vec_a, vec_b, strict=True))


@dataclass
class SpeakerProfile:
    id: str
    name: str
    role: str = "member"  # "owner" | "member" | "guest"
    embedding: list[float] = field(default_factory=list)
    samples_count: int = 1
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VerificationResult:
    verified: bool
    speaker_id: str | None = None
    speaker_name: str | None = None
    role: str | None = None
    score: float = 0.0
    threshold: float = 0.75
    reason: str = ""


class SpeakerManager:
    """Manages enrolled voice profiles, persistence, and realtime verification."""

    def __init__(
        self,
        *,
        profiles_path: Path | None = None,
        enabled: bool = False,
        threshold: float = 0.75,
    ) -> None:
        self.profiles_path = profiles_path
        self.enabled = enabled
        self.threshold = threshold
        self._profiles: dict[str, SpeakerProfile] = {}
        if self.profiles_path:
            self.load()

    @property
    def has_enrolled_speakers(self) -> bool:
        return len(self._profiles) > 0

    def list_speakers(self) -> list[SpeakerProfile]:
        return list(self._profiles.values())

    def get_speaker(self, speaker_id: str) -> SpeakerProfile | None:
        return self._profiles.get(speaker_id)

    def enroll(
        self,
        *,
        speaker_id: str,
        name: str,
        pcm_bytes: bytes,
        role: str = "member",
    ) -> SpeakerProfile:
        """Enroll a new speaker or update an existing one with a voice sample."""
        embedding = AcousticFingerprint.extract(pcm_bytes)
        if embedding is None:
            raise ValueError("Không đủ dữ liệu âm thanh giọng nói để tạo mẫu nhận diện.")

        existing = self._profiles.get(speaker_id)
        if existing and existing.embedding and len(existing.embedding) == len(embedding):
            # Weighted vector update
            n = existing.samples_count
            updated = [
                (existing.embedding[i] * n + embedding[i]) / (n + 1) for i in range(len(embedding))
            ]
            norm = math.sqrt(sum(v * v for v in updated))
            if norm > 0:
                updated = [round(v / norm, 6) for v in updated]
            existing.embedding = updated
            existing.samples_count = n + 1
            existing.name = name
            existing.role = role
            profile = existing
        else:
            profile = SpeakerProfile(
                id=speaker_id,
                name=name,
                role=role,
                embedding=embedding,
                samples_count=1,
                created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            self._profiles[speaker_id] = profile

        self.save()
        log.info(
            "speaker enrolled",
            extra={
                "speaker_id": speaker_id,
                "name": name,
                "samples": profile.samples_count,
            },
        )
        return profile

    def delete(self, speaker_id: str) -> bool:
        """Remove a speaker profile."""
        if speaker_id in self._profiles:
            del self._profiles[speaker_id]
            self.save()
            log.info("speaker deleted", extra={"speaker_id": speaker_id})
            return True
        return False

    def verify(
        self,
        pcm_bytes: bytes,
        threshold: float | None = None,
        force_match: bool = False,
    ) -> VerificationResult:
        """Verify an audio utterance against all enrolled speakers.

        Returns VerificationResult with verification status, matched speaker,
        and cosine similarity score.
        """
        th = threshold if threshold is not None else self.threshold

        # If verification is disabled and force_match is False, allow as open/guest mode
        if not self.enabled and not force_match:
            return VerificationResult(
                verified=True,
                speaker_id=None,
                speaker_name="guest",
                role="guest",
                score=1.0,
                threshold=th,
                reason="verification_disabled",
            )

        if not self._profiles:
            return VerificationResult(
                verified=True,
                speaker_id=None,
                speaker_name="guest",
                role="guest",
                score=1.0,
                threshold=th,
                reason="no_enrolled_profiles",
            )

        test_vec = AcousticFingerprint.extract(pcm_bytes)
        if test_vec is None:
            return VerificationResult(
                verified=False,
                score=0.0,
                threshold=th,
                reason="insufficient_audio",
            )

        best_score = -1.0
        best_speaker: SpeakerProfile | None = None

        for speaker in self._profiles.values():
            if not speaker.embedding:
                continue
            sim = cosine_similarity(test_vec, speaker.embedding)
            if sim > best_score:
                best_score = sim
                best_speaker = speaker

        if best_speaker is not None and best_score >= th:
            return VerificationResult(
                verified=True,
                speaker_id=best_speaker.id,
                speaker_name=best_speaker.name,
                role=best_speaker.role,
                score=round(best_score, 4),
                threshold=th,
                reason="authorized",
            )

        return VerificationResult(
            verified=False,
            speaker_id=best_speaker.id if best_speaker else None,
            speaker_name=best_speaker.name if best_speaker else None,
            score=round(max(0.0, best_score), 4),
            threshold=th,
            reason="unauthorized_speaker",
        )

    def load(self) -> None:
        """Load enrolled profiles from JSON file."""
        if not self.profiles_path or not self.profiles_path.exists():
            return
        try:
            with open(self.profiles_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    prof = SpeakerProfile(**item)
                    self._profiles[prof.id] = prof
                log.info("speakers loaded", extra={"count": len(self._profiles)})
        except Exception:  # noqa: BLE001
            log.warning("failed to load speaker profiles from %s", self.profiles_path)

    def save(self) -> None:
        """Persist enrolled profiles to JSON file."""
        if not self.profiles_path:
            return
        try:
            self.profiles_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.profiles_path, "w", encoding="utf-8") as f:
                json.dump([p.to_dict() for p in self._profiles.values()], f, indent=2)
        except Exception:  # noqa: BLE001
            log.warning("failed to save speaker profiles to %s", self.profiles_path)
