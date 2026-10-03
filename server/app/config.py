"""Typed application settings, loaded from environment / .env."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["google", "mock"]


class Settings(BaseSettings):
    """Every knob the server exposes. Flat on purpose: one env var per knob."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- app
    app_name: str = "smart-apartment-server"
    app_env: Literal["dev", "staging", "prod"] = "dev"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    # ----------------------------------------------------------- security
    api_key: SecretStr | None = None
    device_tokens: dict[str, str] = Field(default_factory=dict)

    # --------------------------------------------------------- descriptors
    home_config_path: Path = Path("config/home.yaml")
    safety_config_path: Path = Path("config/safety.yaml")

    # -------------------------------------------------------------- state
    redis_url: str | None = None
    redis_namespace: str = "sa"
    redis_connect_timeout_s: float = 3.0
    conversation_ttl_s: int = 1800
    conversation_max_turns: int = 12

    # --------------------------------------------------------------- mqtt
    mqtt_enabled: bool = True
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: SecretStr | None = None
    mqtt_client_id: str = "smart-apartment-server"
    mqtt_tls: bool = False
    mqtt_keepalive_s: int = 30
    mqtt_qos: Literal[0, 1, 2] = 1
    mqtt_base_topic: str = "home"
    mqtt_reconnect_min_s: float = 1.0
    mqtt_reconnect_max_s: float = 30.0

    # ----------------------------------------------------------- providers
    stt_provider: Provider = "mock"
    tts_provider: Provider = "mock"
    llm_provider: Provider = "mock"

    google_project_id: str | None = None
    google_location: str = "global"
    google_application_credentials: Path | None = None

    stt_language: str = "vi-VN"
    stt_alt_languages: list[str] = Field(default_factory=list)
    stt_model: str = "long"

    tts_voice: str = "vi-VN-Neural2-A"
    tts_speaking_rate: float = 1.05
    tts_pitch: float = 0.0

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-flash-latest"
    gemini_temperature: float = 0.2
    gemini_max_output_tokens: int = 1024
    llm_timeout_s: float = 15.0

    # -------------------------------------------------------------- audio
    audio_sample_rate: int = 16000
    audio_channels: int = 1
    audio_sample_width: int = 2
    max_utterance_s: float = 20.0
    silence_timeout_ms: int = 900
    tts_chunk_bytes: int = 3200  # 100 ms of 16 kHz mono PCM16

    # ----------------------------------------------------------- limits
    ws_max_sessions: int = 64
    ws_heartbeat_s: float = 20.0
    ws_recv_timeout_s: float = 120.0
    max_audio_bytes_per_utterance: int = 16000 * 2 * 30  # 30 s hard ceiling

    # ------------------------------------------------------------ parsing
    @field_validator("cors_origins", "stt_alt_languages", mode="before")
    @classmethod
    def _split_csv(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("["):
                return json.loads(v)
            return [part.strip() for part in v.split(",") if part.strip()]
        return v

    @field_validator("device_tokens", mode="before")
    @classmethod
    def _parse_tokens(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            return json.loads(v) if v else {}
        return v

    @field_validator("mqtt_qos", mode="before")
    @classmethod
    def _int_literal(cls, v: Any) -> Any:
        # Environment variables arrive as strings, and Literal[0, 1, 2] does not
        # coerce the way a plain `int` annotation would.
        return int(v) if isinstance(v, str) and v.strip().isdigit() else v

    @field_validator("api_key", "gemini_api_key", "mqtt_password", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("redis_url", "mqtt_username", "google_project_id", mode="before")
    @classmethod
    def _blank_str_to_none(cls, v: Any) -> Any:
        return None if isinstance(v, str) and not v.strip() else v

    @model_validator(mode="after")
    def _check_production_posture(self) -> Settings:
        if self.app_env == "prod":
            problems: list[str] = []
            if self.api_key is None:
                problems.append("API_KEY is required when APP_ENV=prod")
            if self.redis_url is None:
                problems.append("REDIS_URL is required when APP_ENV=prod (memory store is dev-only)")
            if self.debug:
                problems.append("DEBUG must be false when APP_ENV=prod")
            if problems:
                raise ValueError("; ".join(problems))
        if self.llm_provider == "google" and self.gemini_api_key is None:
            raise ValueError("LLM_PROVIDER=google requires GEMINI_API_KEY")
        if self.stt_provider == "google" and not self.google_project_id:
            raise ValueError("STT_PROVIDER=google requires GOOGLE_PROJECT_ID")
        return self

    # ------------------------------------------------------------ helpers
    @property
    def auth_enabled(self) -> bool:
        return self.api_key is not None

    @property
    def bytes_per_second(self) -> int:
        return self.audio_sample_rate * self.audio_channels * self.audio_sample_width

    def resolve(self, path: Path) -> Path:
        """Resolve a config path relative to the server package root."""
        return path if path.is_absolute() else (Path(__file__).resolve().parent.parent / path)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
