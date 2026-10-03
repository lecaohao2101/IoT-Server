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


# ------------------------------------------------------- google credentials


def test_blank_credentials_path_means_unset():
    """An empty line in .env must be None, not Path("") -- which resolves to the
    server directory and makes every Google client fail with a confusing error."""
    settings = Settings(_env_file=None, google_application_credentials="")
    assert settings.google_application_credentials is None
    assert Settings(_env_file=None, google_project_id="  ").google_project_id is None


def test_credentials_path_is_exported_to_the_process_environment(tmp_path, monkeypatch):
    """Google's SDKs read os.environ, never our Settings object."""
    from app.ai.registry import apply_google_credentials

    key_file = tmp_path / "service-account.json"
    key_file.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    settings = Settings(
        _env_file=None,
        google_application_credentials=key_file,
        google_project_id="proj-123",
    )
    apply_google_credentials(settings)

    import os

    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == str(key_file)
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "proj-123"


def test_missing_credentials_file_fails_loudly(tmp_path, monkeypatch):
    from app.ai.registry import apply_google_credentials
    from app.core.errors import ConfigError

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    settings = Settings(
        _env_file=None, google_application_credentials=tmp_path / "nope.json"
    )
    with pytest.raises(ConfigError):
        apply_google_credentials(settings)


def test_existing_environment_wins_over_dotenv(tmp_path, monkeypatch):
    """An operator overriding the deployment must not be undone by a stale .env."""
    from app.ai.registry import apply_google_credentials

    key_file = tmp_path / "from-dotenv.json"
    key_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/already/set.json")

    apply_google_credentials(
        Settings(_env_file=None, google_application_credentials=key_file)
    )

    import os

    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == "/already/set.json"


def test_deliberately_open_deployment_still_warns_on_every_boot(caplog):
    """`prod` is refused outright and `staging` needs ALLOW_ANONYMOUS; even then
    the warning must appear, because an open server controls a real apartment."""
    import asyncio
    import logging

    from app.container import AppContainer

    settings = Settings(
        _env_file=None,
        app_env="staging",
        api_key=None,
        allow_anonymous=True,
        redis_url=None,
        mqtt_enabled=False,
    )
    assert not settings.auth_enabled

    async def build():
        container = await AppContainer.create(settings)
        await container.aclose()

    with caplog.at_level(logging.WARNING):
        asyncio.run(build())
    assert any("AUTHENTICATION IS DISABLED" in r.message for r in caplog.records)


# ------------------------------------------------- open-deployment guard


def test_deployed_without_api_key_refuses_to_start():
    """A public URL that actuates an apartment must not run open by accident."""
    with pytest.raises(ValueError, match="ALLOW_ANONYMOUS"):
        Settings(_env_file=None, app_env="staging", api_key=None)


def test_open_deployment_is_allowed_when_asked_for_explicitly():
    settings = Settings(_env_file=None, app_env="staging", api_key=None, allow_anonymous=True)
    assert not settings.auth_enabled


def test_api_key_satisfies_the_guard_without_the_escape_hatch():
    settings = Settings(_env_file=None, app_env="staging", api_key="k")
    assert settings.auth_enabled


def test_dev_still_runs_open_without_ceremony():
    assert Settings(_env_file=None, app_env="dev", api_key=None).auth_enabled is False


# --------------------------------------------- inline google credentials


def test_inline_credentials_are_written_to_a_private_file(monkeypatch):
    """Hosts like Fly deliver secrets as env vars; the SDKs only read paths."""
    import json
    import os

    from app.ai.registry import apply_google_credentials

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    document = {"type": "service_account", "client_email": "bot@example.iam.gserviceaccount.com"}
    apply_google_credentials(
        Settings(_env_file=None, google_credentials_json=json.dumps(document))
    )

    written = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    from pathlib import Path

    assert json.loads(Path(written).read_text(encoding="utf-8")) == document


def test_inline_credentials_win_over_a_file_path(monkeypatch, tmp_path):
    import json
    import os
    from pathlib import Path

    from app.ai.registry import apply_google_credentials

    unused = tmp_path / "from-disk.json"
    unused.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(unused))

    apply_google_credentials(
        Settings(
            _env_file=None,
            google_application_credentials=unused,
            google_credentials_json=json.dumps(
                {"type": "service_account", "client_email": "bot@example.com"}
            ),
        )
    )
    assert Path(os.environ["GOOGLE_APPLICATION_CREDENTIALS"]) != unused


def test_malformed_inline_credentials_fail_at_startup(monkeypatch):
    from app.ai.registry import apply_google_credentials
    from app.core.errors import ConfigError

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    with pytest.raises(ConfigError):
        apply_google_credentials(Settings(_env_file=None, google_credentials_json="not json"))
    with pytest.raises(ConfigError, match="service-account"):
        apply_google_credentials(Settings(_env_file=None, google_credentials_json='{"a": 1}'))


# --------------------------------------------- list settings from env


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("*", ["*"]),
        ("https://a.example", ["https://a.example"]),
        ("https://a.example, https://b.example", ["https://a.example", "https://b.example"]),
        ('["https://a.example"]', ["https://a.example"]),
        ("", []),
    ],
)
def test_cors_origins_accepts_plain_env_strings(monkeypatch, raw, expected):
    """`CORS_ORIGINS=*` must not crash the server at startup.

    pydantic-settings treats a list field as complex and JSON-decodes it before
    any validator runs, so an unquoted `*` raised a parse error and the process
    died before serving a single request. NoDecode hands parsing to us instead.
    """
    monkeypatch.setenv("CORS_ORIGINS", raw)
    assert Settings(_env_file=None).cors_origins == expected


def test_device_tokens_still_require_json(monkeypatch):
    monkeypatch.setenv("DEVICE_TOKENS", '{"esp32": "secret"}')
    assert Settings(_env_file=None).device_tokens == {"esp32": "secret"}


def test_env_example_is_a_usable_starting_point():
    """A newcomer copies this file verbatim; it has to load."""
    from pathlib import Path

    example = Path(__file__).resolve().parent.parent / ".env.example"
    settings = Settings(_env_file=example)
    assert settings.app_env == "dev"
    assert settings.cors_origins == ["*"]


def test_missing_credentials_file_points_at_the_container_fix(tmp_path, monkeypatch):
    """This exact misconfiguration took the deployment down: a path that exists
    on a laptop but is excluded from the image. The message has to name the way
    out, not just the missing file."""
    from app.ai.registry import apply_google_credentials
    from app.core.errors import ConfigError

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    with pytest.raises(ConfigError, match="GOOGLE_CREDENTIALS_JSON"):
        apply_google_credentials(
            Settings(_env_file=None, google_application_credentials=tmp_path / "absent.json")
        )
