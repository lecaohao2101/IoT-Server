"""Tests for acoustic voice biometrics and speaker verification."""

from __future__ import annotations

import base64
import math
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services.session import VoiceSession
from app.services.speaker import (
    AcousticFingerprint,
    SpeakerManager,
    cosine_similarity,
)


def _gen_synthetic_pcm(
    f0: float,
    harmonics: list[float],
    duration_s: float = 1.0,
    sample_rate: int = 16000,
) -> bytes:
    """Generate 16-bit mono PCM bytes for synthetic vocal harmonic frequencies."""
    n_samples = int(duration_s * sample_rate)
    samples = []
    for i in range(n_samples):
        t = i / sample_rate
        val = sum(amp * math.sin(2.0 * math.pi * f0 * h * t) for h, amp in enumerate(harmonics, 1))
        # Hann envelope
        env = 0.5 * (1.0 - math.cos(2.0 * math.pi * i / (n_samples - 1)))
        val = val * env * 0.4
        val = max(-1.0, min(1.0, val))
        samples.append(int(val * 32767.0))
    return struct.pack(f"<{len(samples)}h", *samples)


# ----------------------------------------------------------- Feature Extraction
def test_acoustic_fingerprint_extraction():
    pcm = _gen_synthetic_pcm(140.0, [1.0, 0.8, 0.6, 0.4, 0.2])
    emb = AcousticFingerprint.extract(pcm)
    assert emb is not None
    assert len(emb) == 32  # 12 MFCC + 16 filterbank std + 4 pitch bands
    norm = math.sqrt(sum(v * v for v in emb))
    assert abs(norm - 1.0) < 1e-3


def test_insufficient_audio_returns_none():
    short_pcm = b"\x00" * 200
    assert AcousticFingerprint.extract(short_pcm) is None


def test_speaker_self_similarity_vs_cross_speaker():
    # Speaker A (male, ~130 Hz)
    spk_a_sample1 = _gen_synthetic_pcm(130.0, [1.0, 0.8, 0.6, 0.4, 0.2, 0.1])
    spk_a_sample2 = _gen_synthetic_pcm(132.0, [1.0, 0.79, 0.61, 0.39, 0.21, 0.1])

    # Speaker B (female, ~240 Hz)
    spk_b_sample1 = _gen_synthetic_pcm(240.0, [1.0, 0.7, 0.5, 0.3, 0.15])

    # Speaker D (unregistered, high pitch / child ~320 Hz)
    spk_d_sample1 = _gen_synthetic_pcm(320.0, [1.0, 0.9, 0.4, 0.2])

    emb_a1 = AcousticFingerprint.extract(spk_a_sample1)
    emb_a2 = AcousticFingerprint.extract(spk_a_sample2)
    emb_b = AcousticFingerprint.extract(spk_b_sample1)
    emb_d = AcousticFingerprint.extract(spk_d_sample1)

    assert emb_a1 and emb_a2 and emb_b and emb_d

    self_sim_a = cosine_similarity(emb_a1, emb_a2)
    sim_a_b = cosine_similarity(emb_a1, emb_b)
    sim_a_d = cosine_similarity(emb_a1, emb_d)

    # Self-similarity of Speaker A should be extremely high (> 0.95)
    assert self_sim_a > 0.95
    # Similarity to a completely different voice should be noticeably lower
    assert self_sim_a > sim_a_b
    assert self_sim_a > sim_a_d


# ------------------------------------------------------------- SpeakerManager
def test_speaker_enrollment_and_verification(tmp_path: Path):
    json_path = tmp_path / "speakers.json"
    mgr = SpeakerManager(profiles_path=json_path, enabled=True, threshold=0.75)

    spk_a = _gen_synthetic_pcm(130.0, [1.0, 0.8, 0.6, 0.4, 0.2, 0.1])
    spk_b = _gen_synthetic_pcm(240.0, [1.0, 0.7, 0.5, 0.3, 0.15])
    spk_c = _gen_synthetic_pcm(180.0, [1.0, 0.85, 0.55, 0.35, 0.2])
    spk_d_unregistered = _gen_synthetic_pcm(340.0, [1.0, 0.9, 0.3, 0.1])

    # Enroll A, B, C
    mgr.enroll(speaker_id="person_a", name="Anh A", pcm_bytes=spk_a, role="owner")
    mgr.enroll(speaker_id="person_b", name="Chị B", pcm_bytes=spk_b, role="member")
    mgr.enroll(speaker_id="person_c", name="Em C", pcm_bytes=spk_c, role="member")

    assert len(mgr.list_speakers()) == 3
    assert mgr.has_enrolled_speakers

    # Test verifying A with another sample of A (~131 Hz)
    spk_a_test = _gen_synthetic_pcm(131.0, [1.0, 0.8, 0.6, 0.4, 0.2, 0.1])
    res_a = mgr.verify(spk_a_test)
    assert res_a.verified
    assert res_a.speaker_id == "person_a"
    assert res_a.speaker_name == "Anh A"
    assert res_a.score >= 0.75

    # Test verifying B with another sample of B (~242 Hz)
    spk_b_test = _gen_synthetic_pcm(242.0, [1.0, 0.7, 0.5, 0.3, 0.15])
    res_b = mgr.verify(spk_b_test)
    assert res_b.verified
    assert res_b.speaker_id == "person_b"
    assert res_b.speaker_name == "Chị B"
    assert res_b.score >= 0.75

    # Test verifying unregistered Person D: should be rejected!
    res_d = mgr.verify(spk_d_unregistered)
    assert not res_d.verified
    assert res_d.reason == "unauthorized_speaker"
    assert res_d.score < 0.75

    # Test reloading from disk
    mgr_reloaded = SpeakerManager(profiles_path=json_path, enabled=True, threshold=0.75)
    assert len(mgr_reloaded.list_speakers()) == 3
    assert mgr_reloaded.get_speaker("person_a") is not None


def test_open_mode_when_disabled_or_empty():
    mgr = SpeakerManager(enabled=False)
    pcm = _gen_synthetic_pcm(130.0, [1.0, 0.5])
    res = mgr.verify(pcm)
    assert res.verified
    assert res.reason == "verification_disabled"

    mgr_enabled_empty = SpeakerManager(enabled=True)
    res_empty = mgr_enabled_empty.verify(pcm)
    assert res_empty.verified
    assert res_empty.reason == "no_enrolled_profiles"


# ------------------------------------------------------------- REST Endpoints
def test_rest_speaker_management_endpoints(client: TestClient):
    # 1. List initially empty
    resp = client.get("/api/v1/speakers")
    assert resp.status_code == 200
    data = resp.json()
    assert "speakers" in data
    assert "enabled" in data

    # 2. Enroll a speaker
    pcm = _gen_synthetic_pcm(150.0, [1.0, 0.8, 0.6])
    audio_b64 = base64.b64encode(pcm).decode("ascii")

    enroll_resp = client.post(
        "/api/v1/speakers/enroll",
        json={
            "speaker_id": "test_owner",
            "name": "Chủ nhà Test",
            "role": "owner",
            "audio_base64": audio_b64,
        },
    )
    assert enroll_resp.status_code == 201
    enrolled = enroll_resp.json()
    assert enrolled["id"] == "test_owner"
    assert enrolled["name"] == "Chủ nhà Test"

    # 3. Verify via REST endpoint
    verify_resp = client.post(
        "/api/v1/speakers/verify",
        json={"audio_base64": audio_b64, "threshold": 0.70},
    )
    assert verify_resp.status_code == 200
    ver = verify_resp.json()
    assert ver["verified"]
    assert ver["speaker_id"] == "test_owner"

    # 4. Update settings
    patch_resp = client.patch(
        "/api/v1/speakers/settings",
        json={"enabled": True, "threshold": 0.80},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["enabled"] is True
    assert patch_resp.json()["threshold"] == 0.80

    # 5. Delete speaker
    del_resp = client.delete("/api/v1/speakers/test_owner")
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True


# ------------------------------------------------------------- WebSocket Reject
@pytest.mark.asyncio
async def test_websocket_rejects_unauthorized_speaker_when_enabled(container, tmp_path: Path):
    # Enable speaker verification on container
    mgr = container.speaker_manager
    mgr.profiles_path = tmp_path / "ws_speakers.json"
    mgr.enabled = True
    mgr.threshold = 0.75

    # Enroll Person A
    spk_a = _gen_synthetic_pcm(130.0, [1.0, 0.8, 0.6, 0.4])
    mgr.enroll(speaker_id="person_a", name="Anh A", pcm_bytes=spk_a)

    # Now create VoiceSession
    sent_frames: list[dict] = []

    class MockWs:
        async def send_json(self, payload: dict):
            sent_frames.append(payload)

        async def send_bytes(self, data: bytes):
            pass

    session = VoiceSession(
        MockWs(),
        orchestrator=container.orchestrator,
        recognizer=container.stt,
        settings=container.settings,
        bus=container.bus,
        principal=None,
        speaker_manager=mgr,
    )

    # Feed audio from unregistered Person D (320 Hz)
    spk_d = _gen_synthetic_pcm(320.0, [1.0, 0.9, 0.3])
    session._normalise = lambda p: p
    session._utterance_frames = [spk_d]

    # Mock recognizer transcript
    class MockResult:
        is_final = True
        text = "bật đèn phòng khách"
        confidence = 0.95
        speech_ended = True

    class MockStream:
        async def results(self):
            yield MockResult()

        async def aclose(self):
            pass

    session._stream = MockStream()
    await session._consume_transcripts()

    # Verify that the session rejected Person D and sent unauthorized message
    kinds = [f["type"] for f in sent_frames]
    assert "stt.final" in kinds
    assert "assistant.final" in kinds
    final_frame = next(f for f in sent_frames if f["type"] == "assistant.final")
    assert "không nhận diện được giọng nói" in final_frame["text"]
    assert final_frame.get("plan") is None
    assert final_frame.get("error") == "unauthorized_speaker"
