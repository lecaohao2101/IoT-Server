"""Provider selection.

Each stage is chosen independently, so a deployment can run Google STT with the
offline reasoner, or Gemini with the mock microphone, while developing. A provider
that fails to construct degrades to its mock outside production and hard-fails
inside it -- silently serving a tone generator to real users would be worse than
refusing to boot.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path

from app.ai.base import LanguageModel, SpeechRecognizer, SpeechSynthesizer
from app.ai.llm.mock import MockLanguageModel
from app.ai.stt.mock import MockRecognizer
from app.ai.tts.mock import MockSynthesizer
from app.config import Settings
from app.core.errors import ConfigError
from app.domain.home import HomeConfig

log = logging.getLogger(__name__)


def apply_google_credentials(settings: Settings) -> None:
    """Export the service-account path into the process environment.

    Google's client libraries resolve credentials from ``GOOGLE_APPLICATION_CREDENTIALS``
    in ``os.environ``; they never see our ``Settings`` object. Without this, a path
    configured in ``.env`` is silently ignored and every call fails with a confusing
    "credentials missing" error. Must run before any client is constructed.

    A value already present in the real environment wins -- an operator overriding
    the deployment should not be undone by a stale ``.env``.
    """
    project = settings.google_project_id
    if project:
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project)

    inline = settings.google_credentials_json
    if inline is not None:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_materialise(inline.get_secret_value()))
        log.info("google credentials loaded from GOOGLE_CREDENTIALS_JSON")
        return

    configured = settings.google_application_credentials
    if configured is None:
        return

    resolved = settings.resolve(configured)
    if not resolved.is_file():
        # The usual cause in a container: credentials/ is excluded from the
        # image, so a path that works on a laptop points at nothing once
        # deployed. Say so, rather than leaving a bare "file not found".
        raise ConfigError(
            f"GOOGLE_APPLICATION_CREDENTIALS points at a missing file: {resolved}. "
            "In a container the credentials directory is usually not part of the "
            "image -- set GOOGLE_CREDENTIALS_JSON to the contents of the "
            "service-account document instead of a path."
        )
    os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(resolved))
    log.info("google credentials configured", extra={"path": str(resolved)})


def _materialise(raw: str) -> Path:
    """Write an inline service account to a private file the SDKs can open.

    Google's libraries only read credentials from a path, so a secret delivered
    as an environment variable has to land on disk somewhere. It goes to the
    process temp directory with owner-only permissions, never into the project.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"GOOGLE_CREDENTIALS_JSON is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or "client_email" not in parsed:
        raise ConfigError("GOOGLE_CREDENTIALS_JSON is not a service-account document")

    path = Path(tempfile.gettempdir()) / "smart-apartment-google-credentials.json"
    path.write_text(raw, encoding="utf-8")
    with contextlib.suppress(OSError):  # no-op on Windows, meaningful on Linux
        path.chmod(0o600)
    return path


def _fail_or_fallback(settings: Settings, stage: str, exc: Exception, fallback):
    if settings.app_env == "prod":
        raise ConfigError(f"{stage} provider could not be initialised: {exc}") from exc
    log.error(
        "%s provider unavailable, falling back to mock", stage, extra={"error": str(exc)}
    )
    return fallback


def build_recognizer(settings: Settings) -> SpeechRecognizer:
    if settings.stt_provider != "google":
        return MockRecognizer()
    try:
        from app.ai.stt.google_v2 import GoogleRecognizer

        return GoogleRecognizer(
            project_id=settings.google_project_id or "",
            location=settings.google_location,
            language=settings.stt_language,
            alt_languages=tuple(settings.stt_alt_languages),
            model=settings.stt_model,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail_or_fallback(settings, "STT", exc, MockRecognizer())


def build_synthesizer(settings: Settings) -> SpeechSynthesizer:
    if settings.tts_provider != "google":
        return MockSynthesizer()
    try:
        from app.ai.tts.google import GoogleSynthesizer

        return GoogleSynthesizer(
            voice=settings.tts_voice,
            language=settings.stt_language,
            speaking_rate=settings.tts_speaking_rate,
            pitch=settings.tts_pitch,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail_or_fallback(settings, "TTS", exc, MockSynthesizer())


def build_language_model(settings: Settings, home: HomeConfig) -> LanguageModel:
    if settings.llm_provider != "google":
        return MockLanguageModel(home)
    try:
        from app.ai.llm.gemini import GeminiLanguageModel

        return GeminiLanguageModel(
            api_key=settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else "",
            model=settings.gemini_model,
            temperature=settings.gemini_temperature,
            max_output_tokens=settings.gemini_max_output_tokens,
            timeout_s=settings.llm_timeout_s,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail_or_fallback(settings, "LLM", exc, MockLanguageModel(home))
