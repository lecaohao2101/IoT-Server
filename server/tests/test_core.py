"""Unit tests for the primitives the pipeline leans on."""

from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.core.audio import (
    AudioFormat,
    SilenceEndpointer,
    iter_frames,
    pcm_to_wav,
    resample,
    rms,
    to_mono,
    wav_to_pcm,
)
from app.core.errors import UnauthorizedError
from app.core.json_stream import (
    SentenceChunker,
    StreamingStringField,
    extract_json_object,
)
from app.core.ratelimit import KeyedRateLimiter, TokenBucket
from app.core.security import Principal, authenticate, extract_bearer
from app.core.utils import fold, strip_accents

# ------------------------------------------------------------------ streaming JSON


def test_streaming_field_decodes_across_chunk_boundaries():
    extractor = StreamingStringField("speech")
    chunks = ['{"spe', 'ech": "Đã b', 'ật đèn', ' phòng khách."', ', "commands": []}']
    out = "".join(extractor.feed(c) for c in chunks)
    assert out == "Đã bật đèn phòng khách."
    assert extractor.done


def test_streaming_field_handles_escapes_split_mid_sequence():
    extractor = StreamingStringField("speech")
    pieces = ['{"speech": "a\\', 'nb \\u00e9 c"}']
    out = "".join(extractor.feed(p) for p in pieces)
    assert out == "a\nb é c"


def test_streaming_field_stops_at_closing_quote():
    extractor = StreamingStringField("speech")
    extractor.feed('{"speech": "xong", "commands": [{"value": "ignored"}]}')
    assert extractor.value == "xong"
    assert extractor.feed("more") == ""


def test_extract_json_object_tolerates_fences_and_trailing_prose():
    raw = '```json\n{"speech": "ok", "commands": []}\n```'
    assert extract_json_object(raw)["speech"] == "ok"
    assert extract_json_object('noise {"a": {"b": 1}} tail')["a"] == {"b": 1}


def test_extract_json_object_rejects_garbage():
    with pytest.raises(ValueError):
        extract_json_object("not json at all")


def test_sentence_chunker_emits_clauses_then_flushes():
    chunker = SentenceChunker()
    emitted = chunker.feed("Đã bật đèn phòng khách. Bạn cần gì thêm")
    assert emitted == ["Đã bật đèn phòng khách."]
    assert chunker.flush() == "Bạn cần gì thêm"


def test_sentence_chunker_keeps_short_fragments_together():
    chunker = SentenceChunker()
    assert chunker.feed("Ok.") == []  # below min_len: do not synthesise two characters
    assert chunker.flush() == "Ok."


# ------------------------------------------------------------------------- audio


def test_wav_roundtrip_preserves_payload_and_rate():
    pcm = bytes(range(0, 256)) * 4
    fmt = AudioFormat(sample_rate=16000)
    recovered, parsed = wav_to_pcm(pcm_to_wav(pcm, fmt))
    assert recovered == pcm
    assert parsed.sample_rate == 16000
    assert parsed.channels == 1
    assert parsed.sample_width == 2


def test_wav_to_pcm_passes_raw_pcm_through():
    raw = b"\x01\x02\x03\x04"
    payload, _ = wav_to_pcm(raw)
    assert payload == raw


def test_resample_halves_sample_count_when_rate_halves():
    pcm = b"\x00\x10" * 1000
    out = resample(pcm, 32000, 16000)
    assert abs(len(out) // 2 - 500) <= 2


def test_to_mono_averages_channels():
    # Two frames: (1000, 2000) and (0, 0)
    stereo = (1000).to_bytes(2, "little", signed=True) + (2000).to_bytes(2, "little", signed=True)
    stereo += b"\x00\x00\x00\x00"
    mono = to_mono(stereo, 2)
    assert int.from_bytes(mono[:2], "little", signed=True) == 1500


def test_rms_distinguishes_silence_from_tone():
    silence = b"\x00\x00" * 320
    loud = (12000).to_bytes(2, "little", signed=True) * 320
    assert rms(silence) == 0
    assert rms(loud) > 10000


def test_iter_frames_covers_whole_buffer():
    data = bytes(1000)
    frames = list(iter_frames(data, 320))
    assert sum(len(f) for f in frames) == 1000
    assert len(frames) == 4


def test_endpointer_fires_after_speech_then_silence():
    fmt = AudioFormat(sample_rate=16000)
    endpointer = SilenceEndpointer(fmt, silence_ms=200, min_speech_ms=100)
    frame_speech = (9000).to_bytes(2, "little", signed=True) * 320  # 20 ms
    frame_silence = b"\x00\x00" * 320

    fired = any(endpointer.accept(frame_speech) for _ in range(10))
    assert not fired, "should not end while the user is still talking"
    assert endpointer.speech_detected

    fired = any(endpointer.accept(frame_silence) for _ in range(20))
    assert fired


def test_endpointer_ignores_silence_before_any_speech():
    endpointer = SilenceEndpointer(AudioFormat(), silence_ms=100, min_speech_ms=100)
    frame = b"\x00\x00" * 320
    assert not any(endpointer.accept(frame) for _ in range(50))


# -------------------------------------------------------------------- rate limits


def test_token_bucket_refuses_past_capacity_then_refills():
    bucket = TokenBucket(capacity=2, refill_per_second=10)
    assert bucket.try_consume(now=0.0)
    assert bucket.try_consume(now=0.0)
    assert not bucket.try_consume(now=0.0)
    assert bucket.try_consume(now=0.5)


def test_keyed_limiter_isolates_keys():
    limiter = KeyedRateLimiter(capacity=1, refill_per_second=0)
    assert limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")


# ------------------------------------------------------------------------ security


def test_extract_bearer_handles_schemes_and_bare_tokens():
    assert extract_bearer("Bearer abc") == "abc"
    assert extract_bearer("token abc") == "abc"
    assert extract_bearer("abc") == "abc"
    assert extract_bearer(None) is None


def test_auth_disabled_returns_anonymous():
    settings = Settings(_env_file=None, api_key=None)
    assert authenticate(settings, token=None).kind == "anonymous"


def test_api_key_and_device_token_are_distinguished():
    settings = Settings(
        _env_file=None, api_key="app-secret", device_tokens={"esp32_living": "device-secret"}
    )
    assert authenticate(settings, token="app-secret") == Principal(kind="app", id="mobile")
    assert authenticate(settings, token="device-secret").id == "esp32_living"
    with pytest.raises(UnauthorizedError):
        authenticate(settings, token="wrong")
    with pytest.raises(UnauthorizedError):
        authenticate(settings, token=None)


# --------------------------------------------------------------------- text utils


def test_strip_accents_folds_vietnamese():
    assert strip_accents("Đèn Phòng Khách") == "den phong khach"
    assert fold("  Đèn   khách ") == "đèn khách"


def test_settings_reject_insecure_production_posture():
    with pytest.raises(ValueError):
        Settings(_env_file=None, app_env="prod", api_key=None, redis_url="redis://x")
    with pytest.raises(ValueError):
        Settings(_env_file=None, app_env="prod", api_key="k", redis_url=None)


def test_settings_parse_json_device_tokens():
    settings = Settings(_env_file=None, device_tokens=json.dumps({"a": "b"}))
    assert settings.device_tokens == {"a": "b"}
