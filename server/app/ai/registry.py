"""Provider selection.

Each stage is chosen independently, so a deployment can run Google STT with the
offline reasoner, or Gemini with the mock microphone, while developing. A provider
that fails to construct degrades to its mock outside production and hard-fails
inside it -- silently serving a tone generator to real users would be worse than
refusing to boot.
"""

from __future__ import annotations

import logging

from app.ai.base import LanguageModel, SpeechRecognizer, SpeechSynthesizer
from app.ai.llm.mock import MockLanguageModel
from app.ai.stt.mock import MockRecognizer
from app.ai.tts.mock import MockSynthesizer
from app.config import Settings
from app.core.errors import ConfigError
from app.domain.home import HomeConfig

log = logging.getLogger(__name__)


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
