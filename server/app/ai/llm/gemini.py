"""Gemini reasoning stage via the google-genai SDK.

Constrained decoding is requested with a JSON schema so the orchestrator can rely
on the shape of the answer. The SDK has moved the schema parameter around between
releases, so the config is built once and retried against the older name rather
than pinning users to a single SDK version.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.ai.base import LanguageModel, LlmMessage
from app.core.errors import ProviderError, ProviderTimeoutError

log = logging.getLogger(__name__)


class GeminiLanguageModel(LanguageModel):
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-flash-latest",
        temperature: float = 0.2,
        max_output_tokens: int = 1024,
        timeout_s: float = 15.0,
    ) -> None:
        if not api_key:
            raise ProviderError("GEMINI_API_KEY is required for the Gemini provider")
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._timeout = timeout_s
        self._client = None
        self.name = f"gemini:{model}"

    def _ensure_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
            log.info("gemini client ready", extra={"model": self._model})
        return self._client

    def _build_config(self, system: str, json_schema: dict[str, Any] | None):
        from google.genai import types

        base: dict[str, Any] = {
            "system_instruction": system,
            "temperature": self._temperature,
            "max_output_tokens": self._max_output_tokens,
            "candidate_count": 1,
        }
        if json_schema is None:
            return types.GenerateContentConfig(**base)

        base["response_mime_type"] = "application/json"
        for schema_field in ("response_json_schema", "response_schema"):
            try:
                return types.GenerateContentConfig(**base, **{schema_field: json_schema})
            except Exception:  # noqa: BLE001 - field name differs across SDK releases
                continue
        log.warning("SDK rejected both schema fields; falling back to prompt-only JSON")
        return types.GenerateContentConfig(**base)

    @staticmethod
    def _to_contents(messages: list[LlmMessage]):
        from google.genai import types

        return [
            types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[types.Part.from_text(text=m.content)],
            )
            for m in messages
            if m.content
        ]

    async def stream(
        self,
        *,
        system: str,
        messages: list[LlmMessage],
        json_schema: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        client = self._ensure_client()
        config = self._build_config(system, json_schema)
        contents = self._to_contents(messages)

        # The budget is per step, not for the whole generation: the consumer
        # synthesises speech between chunks, and that time is not Gemini's fault.
        try:
            stream = await asyncio.wait_for(
                client.aio.models.generate_content_stream(
                    model=self._model, contents=contents, config=config
                ),
                timeout=self._timeout,
            )
            iterator = stream.__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(iterator.__anext__(), timeout=self._timeout)
                except StopAsyncIteration:
                    break
                text = getattr(chunk, "text", None)
                if text:
                    yield text
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                f"Gemini timed out after {self._timeout:g}s", details={"model": self._model}
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                f"Gemini request failed: {exc}", details={"model": self._model}
            ) from exc
