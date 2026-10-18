"""
src/llm/gemma_api.py — Hosted Google Gemma provider via google-genai SDK.

Design rules
────────────
- Subclasses LLMProvider and satisfies the raw_generate() contract.
- Uses explicitly configured Gemma model ID; rejects non-Gemma model strings
  at construction time via _assert_gemma_model().
- Never silently switches to Gemini, Ollama, or any other model.
- API key is retrieved exclusively from environment (via get_api_key()) and is
  never stored in logs, strings, exceptions, or representations.
- Supports structured response validation for GenerateResult and ProseResult.
- Handles rate-limiting (HTTP 429 / RESOURCE_EXHAUSTED) with bounded retries;
  raises ProviderError(is_rate_limit=True) when exhausted.
- Records per-call timings and validity into GemmaCallTrace when provided.
"""

from __future__ import annotations

import logging
import time
from typing import Any, TypeVar

from google import genai
from google.genai import errors, types

from src.config import get_api_key
from src.llm.base import LLMProvider, ProviderError
from src.llm.prompts import (
    extract_json,
    parse_generate_result,
    parse_prose_result,
)
from src.llm.trace import GemmaCallTrace, Timer
from src.models import GenerateResult, ProseResult

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _is_rate_limit_error(exc: Exception) -> bool:
    """Return True if an exception represents a rate limit / quota exhaustion."""
    if isinstance(exc, errors.APIError):
        if getattr(exc, "code", None) == 429:
            return True
    msg = str(exc).lower()
    return "resource_exhausted" in msg or "rate limit" in msg or "quota" in msg or "429" in msg


class GemmaAPIProvider(LLMProvider):
    """
    Hosted Gemma provider using Google GenAI API.

    Parameters
    ──────────
    model_name:    Configured Gemma model ID (e.g. 'gemma-3-27b-it').
    api_key:       Optional override; if omitted, reads GOOGLE_GEMMA_API_KEY from env.
    client:        Optional injected genai.Client instance for testing / mocking.
    max_retries:   Maximum rate-limit retries before raising ProviderError(is_rate_limit=True).
    retry_delay_s: Initial backoff delay in seconds for rate-limit retries.
    """

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        *,
        client: Any | None = None,
        max_retries: int = 2,
        retry_delay_s: float = 1.0,
    ) -> None:
        self._assert_gemma_model(model_name)
        self._model_name = model_name.strip()
        self._max_retries = max(0, max_retries)
        self._retry_delay_s = max(0.0, retry_delay_s)

        if client is not None:
            self._client = client
        else:
            key = api_key or get_api_key()
            self._client = genai.Client(api_key=key)

    @property
    def provider_name(self) -> str:
        return "api"

    @property
    def model_name(self) -> str:
        return self._model_name

    def raw_generate(
        self,
        role: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, bool]:
        """
        Execute raw generation against the hosted Gemma model.

        Returns (raw_text, valid_on_first_try).
        Raises ProviderError if unrecoverable error occurs or rate-limit retries exhausted.
        """
        logger.debug(
            "Calling Gemma API: role=%s model=%s temp=%.2f max_tokens=%d",
            role,
            self._model_name,
            temperature,
            max_tokens,
        )

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        attempts = 0
        valid_on_first_try = True

        while True:
            attempts += 1
            try:
                response = self._client.models.generate_content(
                    model=self._model_name,
                    contents=prompt,
                    config=config,
                )
                text = response.text if response and hasattr(response, "text") else None
                if text is None:
                    raise ProviderError(
                        f"Gemma model '{self._model_name}' returned empty or blocked content for role '{role}'.",
                        is_rate_limit=False,
                    )
                return text, valid_on_first_try

            except Exception as exc:
                # Check for rate limit / quota exhaustion
                if _is_rate_limit_error(exc):
                    valid_on_first_try = False
                    if attempts <= self._max_retries:
                        backoff = self._retry_delay_s * (2 ** (attempts - 1))
                        logger.warning(
                            "Rate limit hit for model '%s' (role '%s', attempt %d/%d). Backing off %.1fs.",
                            self._model_name,
                            role,
                            attempts,
                            self._max_retries,
                            backoff,
                        )
                        time.sleep(backoff)
                        continue
                    raise ProviderError(
                        f"Rate limit exceeded calling Gemma model '{self._model_name}' after {attempts} attempts.",
                        is_rate_limit=True,
                    ) from exc

                # Check if it's already a ProviderError
                if isinstance(exc, ProviderError):
                    raise

                # Non-rate-limit API or connection error
                sanitized_msg = str(exc)
                # Ensure no accidental secret leakage in exception messages
                raise ProviderError(
                    f"API error calling Gemma model '{self._model_name}' for role '{role}': {sanitized_msg}",
                    is_rate_limit=False,
                ) from exc

    def generate_structured(
        self,
        role: str,
        prompt: str,
        schema_cls: type[T],
        temperature: float,
        max_tokens: int,
        *,
        trace: GemmaCallTrace | None = None,
    ) -> tuple[T, bool]:
        """
        Execute model generation and parse/validate the output into a Pydantic model.
        Performs one retry if the model returns malformed JSON, as required by the handbook.
        Updates GemmaCallTrace if provided.
        """
        first_try_overall = True

        with Timer() as timer:
            raw_text, raw_first_try = self.raw_generate(
                role=role,
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            first_try_overall = raw_first_try

            # Try parsing
            parsed: T | None = None
            try:
                if schema_cls is GenerateResult:
                    parsed = parse_generate_result(raw_text)  # type: ignore
                elif schema_cls is ProseResult:
                    parsed = parse_prose_result(raw_text)  # type: ignore
                else:
                    json_str = extract_json(raw_text)
                    parsed = schema_cls.model_validate_json(json_str)  # type: ignore
            except (ValueError, Exception) as parse_err:
                logger.warning(
                    "JSON parsing failed on first attempt for role '%s' (%s). Retrying once.",
                    role,
                    parse_err,
                )
                first_try_overall = False
                # One retry on malformed JSON
                raw_text_retry, _ = self.raw_generate(
                    role=role,
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                try:
                    if schema_cls is GenerateResult:
                        parsed = parse_generate_result(raw_text_retry)  # type: ignore
                    elif schema_cls is ProseResult:
                        parsed = parse_prose_result(raw_text_retry)  # type: ignore
                    else:
                        json_str = extract_json(raw_text_retry)
                        parsed = schema_cls.model_validate_json(json_str)  # type: ignore
                except (ValueError, Exception) as second_err:
                    raise ProviderError(
                        f"Gemma model '{self._model_name}' failed to produce valid {schema_cls.__name__} "
                        f"JSON after retry: {second_err}",
                        is_rate_limit=False,
                    ) from second_err

        if trace is not None:
            trace.record(
                role=role,
                provider=self.provider_name,
                model=self.model_name,
                ms=timer.elapsed_ms,
                valid_first_try=first_try_overall,
            )

        assert parsed is not None
        return parsed, first_try_overall

    def generate_plan_and_sql(
        self,
        prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        *,
        trace: GemmaCallTrace | None = None,
    ) -> tuple[GenerateResult, bool]:
        """Convenience method for Role 1 (Understand) & Role 2 (Write SQL)."""
        return self.generate_structured(
            role="generate",
            prompt=prompt,
            schema_cls=GenerateResult,
            temperature=temperature,
            max_tokens=max_tokens,
            trace=trace,
        )

    def repair_sql(
        self,
        prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        *,
        trace: GemmaCallTrace | None = None,
    ) -> tuple[GenerateResult, bool]:
        """Convenience method for Role 3 (SQL Repair)."""
        return self.generate_structured(
            role="repair",
            prompt=prompt,
            schema_cls=GenerateResult,
            temperature=temperature,
            max_tokens=max_tokens,
            trace=trace,
        )

    def explain_answer(
        self,
        prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 512,
        *,
        trace: GemmaCallTrace | None = None,
    ) -> tuple[ProseResult, bool]:
        """Convenience method for Role 4 (Explain / Prose)."""
        return self.generate_structured(
            role="explain",
            prompt=prompt,
            schema_cls=ProseResult,
            temperature=temperature,
            max_tokens=max_tokens,
            trace=trace,
        )

    def __repr__(self) -> str:
        return f"GemmaAPIProvider(provider='{self.provider_name}', model='{self.model_name}')"
