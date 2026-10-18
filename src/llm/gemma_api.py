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
import random
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
        code = getattr(exc, "code", None)
        status = getattr(exc, "status", None)
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            return True
    msg = str(exc).lower()
    return "resource_exhausted" in msg or "rate limit" in msg or "quota" in msg or "429" in msg


def _is_permanent_error(exc: Exception) -> bool:
    """Return True if an error is permanent and should never be retried (400, 401, 403, 404)."""
    if isinstance(exc, errors.APIError):
        code = getattr(exc, "code", None)
        status = getattr(exc, "status", None)
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            return False
        if code in (400, 401, 403, 404):
            return True
        if status in ("INVALID_ARGUMENT", "UNAUTHENTICATED", "PERMISSION_DENIED", "NOT_FOUND"):
            return True
        if isinstance(exc, errors.ClientError) and code != 429:
            return True

    msg = str(exc).lower()
    if any(k in msg for k in ("unauthenticated", "invalid api key", "permission_denied", "permission denied", "not_found", "model not found")):
        return True
    return False


def _is_transient_error(exc: Exception) -> bool:
    """Return True if an error is transient (HTTP 5xx, 429 / quota, network timeout) and can be retried."""
    if _is_permanent_error(exc):
        return False

    if isinstance(exc, errors.APIError):
        code = getattr(exc, "code", None)
        status = getattr(exc, "status", None)
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            return True
        if code is not None and 500 <= code < 600:
            return True
        if status in ("INTERNAL", "UNAVAILABLE", "DEADLINE_EXCEEDED"):
            return True

    if isinstance(exc, errors.ServerError):
        return True

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    msg = str(exc).lower()
    if any(k in msg for k in (
        "resource_exhausted", "rate limit", "quota", "429",
        "500", "502", "503", "504", "internal error", "internal",
        "unavailable", "service unavailable", "deadline exceeded",
        "timeout", "timed out", "connection reset"
    )):
        return True

    return False


def _is_mock(obj: Any) -> bool:
    return obj is not None and type(obj).__module__.startswith("unittest.mock")


def _extract_response_content(response: Any, model_name: str, role: str) -> str:
    """
    Safely extract text from GenerateContentResponse, inspecting finish_reason and safety metadata.
    Raises ProviderError with explicit metadata if empty or blocked.
    """
    if response is None:
        raise ProviderError(
            f"Gemma model '{model_name}' returned empty or blocked content for role '{role}'.",
            is_rate_limit=False,
        )

    # Check candidates and finish reason
    candidates = getattr(response, "candidates", None)
    if _is_mock(candidates):
        candidates = None

    finish_reason = None
    safety_ratings = None
    if candidates and len(candidates) > 0:
        first_candidate = candidates[0]
        fr = getattr(first_candidate, "finish_reason", None)
        if not _is_mock(fr):
            finish_reason = fr
        sr = getattr(first_candidate, "safety_ratings", None)
        if not _is_mock(sr):
            safety_ratings = sr

    # Check prompt feedback
    prompt_feedback = getattr(response, "prompt_feedback", None)
    block_reason = None
    if prompt_feedback and not _is_mock(prompt_feedback):
        br = getattr(prompt_feedback, "block_reason", None)
        if not _is_mock(br):
            block_reason = br

    # Check if output was truncated at token limit (MAX_TOKENS finish reason)
    # Truncated output is unusable for structured JSON parsing; treat as retriable.
    if finish_reason is not None:
        finish_str = str(finish_reason).upper()
        if "MAX_TOKENS" in finish_str or finish_str == "FINISHREASON.MAX_TOKENS":
            logger.warning(
                "Gemma output truncated (MAX_TOKENS) for role '%s' — output is incomplete and cannot be parsed.",
                role,
            )
            raise ProviderError(
                f"Gemma model '{model_name}' output truncated (MAX_TOKENS) for role '{role}': "
                f"increase max_output_tokens or shorten the prompt.",
                is_rate_limit=False,
            )

    # Check if blocked by safety / policy
    finish_reason_str = str(finish_reason).upper() if finish_reason else ""
    block_reason_str = str(block_reason).upper() if block_reason else ""
    is_blocked = (
        (block_reason_str and block_reason_str not in ("NONE", "BLOCK_REASON_UNSPECIFIED", ""))
        or any(reason in finish_reason_str for reason in ("SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"))
    )

    if is_blocked:
        metadata_str = f"finish_reason={finish_reason}"
        if block_reason:
            metadata_str += f", block_reason={block_reason}"
        if safety_ratings:
            metadata_str += f", safety_ratings={safety_ratings}"
        logger.warning("Gemma response blocked: %s", metadata_str)
        raise ProviderError(
            f"Gemma model '{model_name}' response blocked for role '{role}': {metadata_str}",
            is_rate_limit=False,
        )

    # Extract text safely
    text = None
    try:
        t = getattr(response, "text", None)
        if not _is_mock(t):
            text = t
    except Exception as text_err:
        logger.debug("Accessing response.text raised exception: %s", text_err)

    if not text and candidates:
        content = getattr(candidates[0], "content", None)
        if not _is_mock(content):
            parts = getattr(content, "parts", None)
            if parts and not _is_mock(parts):
                extracted = []
                for part in parts:
                    p_text = getattr(part, "text", None)
                    if p_text and not _is_mock(p_text):
                        extracted.append(p_text)
                if extracted:
                    text = "".join(extracted)

    if text is None or not text.strip():
        metadata_str = f" ({finish_reason})" if finish_reason else ""
        raise ProviderError(
            f"Gemma model '{model_name}' returned empty or blocked content for role '{role}'{metadata_str}.",
            is_rate_limit=False,
        )

    return text.strip()



class GemmaAPIProvider(LLMProvider):
    """
    Hosted Gemma provider using Google GenAI API.

    Parameters
    ──────────
    model_name:    Configured Gemma model ID (e.g. 'gemma-3-27b-it').
    api_key:       Optional override; if omitted, reads GOOGLE_GEMMA_API_KEY from env.
    client:        Optional injected genai.Client instance for testing / mocking.
    max_retries:   Maximum transient error retries (HTTP 5xx, 429, timeouts).
    retry_delay_s: Initial backoff delay in seconds for transient retries.
    """

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        *,
        client: Any | None = None,
        max_retries: int = 3,
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
        Raises ProviderError if unrecoverable error occurs or retries exhausted.
        """
        logger.info(
            "Gemma generation starting: role=%s model=%s temp=%.2f max_tokens=%d",
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
            t_attempt = time.monotonic()
            logger.debug(
                "Attempt %d/%d for role '%s' on model '%s'",
                attempts,
                self._max_retries + 1,
                role,
                self._model_name,
            )
            try:
                response = self._client.models.generate_content(
                    model=self._model_name,
                    contents=prompt,
                    config=config,
                )
                text = _extract_response_content(response, self._model_name, role)
                elapsed_ms = int((time.monotonic() - t_attempt) * 1000)
                logger.info(
                    "Gemma generation succeeded: role=%s model=%s attempt=%d elapsed=%dms",
                    role,
                    self._model_name,
                    attempts,
                    elapsed_ms,
                )
                return text, valid_on_first_try

            except Exception as exc:
                elapsed_ms = int((time.monotonic() - t_attempt) * 1000)

                # If already a ProviderError from content inspection (empty/blocked), do not retry
                if isinstance(exc, ProviderError):
                    logger.warning(
                        "Gemma content error: role=%s model=%s attempt=%d elapsed=%dms error=%s",
                        role,
                        self._model_name,
                        attempts,
                        elapsed_ms,
                        exc,
                    )
                    raise

                # Check if transient error (5xx, 429, timeout)
                if _is_transient_error(exc):
                    valid_on_first_try = False
                    is_rate_limit = _is_rate_limit_error(exc)
                    if attempts <= self._max_retries:
                        backoff = self._retry_delay_s * (2 ** (attempts - 1))
                        jitter = random.uniform(0.0, 0.5 * backoff)
                        actual_delay = backoff + jitter
                        logger.warning(
                            "Transient error calling Gemma (role=%s, model=%s, attempt=%d/%d, elapsed=%dms): %s. "
                            "Retrying in %.2fs with backoff and jitter.",
                            role,
                            self._model_name,
                            attempts,
                            self._max_retries + 1,
                            elapsed_ms,
                            exc,
                            actual_delay,
                        )
                        time.sleep(actual_delay)
                        continue

                    # Retries exhausted
                    sanitized_msg = str(exc)
                    logger.error(
                        "Gemma retries exhausted after %d attempts: role=%s model=%s elapsed=%dms error=%s",
                        attempts,
                        role,
                        self._model_name,
                        elapsed_ms,
                        sanitized_msg,
                    )
                    if is_rate_limit:
                        err_prefix = f"Rate limit exceeded calling Gemma model '{self._model_name}' after {attempts} attempts"
                    else:
                        err_prefix = f"Gemma model '{self._model_name}' failed after {attempts} attempts for role '{role}'"
                    raise ProviderError(
                        f"{err_prefix}: {sanitized_msg}",
                        is_rate_limit=is_rate_limit,
                    ) from exc


                # Permanent error (400, 401, 403, 404, etc.)
                sanitized_msg = str(exc)
                logger.error(
                    "Permanent error calling Gemma (non-retryable): role=%s model=%s attempt=%d elapsed=%dms error=%s",
                    role,
                    self._model_name,
                    attempts,
                    elapsed_ms,
                    sanitized_msg,
                )
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
        Updates GemmaCallTrace if provided, including on failure.
        """
        first_try_overall = True

        with Timer() as timer:
            raw_text: str | None = None
            initial_raw_error: Exception | None = None

            try:
                raw_text, raw_first_try = self.raw_generate(
                    role=role,
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                first_try_overall = raw_first_try
            except ProviderError as p_err:
                # MAX_TOKENS truncation: the model ran out of tokens before emitting JSON.
                # Treat as a parse failure so the JSON-retry path runs with the nudge prompt.
                err_str = str(p_err).lower()
                if "max_tokens" in err_str or "truncated" in err_str:
                    logger.warning(
                        "MAX_TOKENS truncation for role '%s' — will retry with JSON nudge prompt.",
                        role,
                    )
                    initial_raw_error = p_err
                    first_try_overall = False
                else:
                    # Permanent or unrecoverable provider error — record trace and bubble up
                    if trace is not None:
                        trace.record(
                            role=role,
                            provider=self.provider_name,
                            model=self.model_name,
                            ms=timer.elapsed_ms,
                            valid_first_try=False,
                        )
                    raise
            except Exception:
                if trace is not None:
                    trace.record(
                        role=role,
                        provider=self.provider_name,
                        model=self.model_name,
                        ms=timer.elapsed_ms,
                        valid_first_try=False,
                    )
                raise

            # Try parsing (skip if we already have a raw error from truncation)
            parsed: T | None = None
            parse_err_for_retry: Exception | None = initial_raw_error

            if initial_raw_error is None:
                try:
                    if schema_cls is GenerateResult:
                        parsed = parse_generate_result(raw_text)  # type: ignore
                    elif schema_cls is ProseResult:
                        parsed = parse_prose_result(raw_text)  # type: ignore
                    else:
                        json_str = extract_json(raw_text)  # type: ignore
                        parsed = schema_cls.model_validate_json(json_str)  # type: ignore
                except (ValueError, Exception) as parse_err:
                    parse_err_for_retry = parse_err

            if parse_err_for_retry is not None:
                logger.warning(
                    "JSON parsing failed on first attempt for role '%s' (%s). Retrying once.",
                    role,
                    parse_err_for_retry,
                )
                first_try_overall = False
                # One retry on malformed JSON — append an explicit JSON-only reminder
                # and lower temperature to discourage prose/filler output.
                json_nudge = (
                    "\n\n[SYSTEM REMINDER] Your previous response did not contain a valid JSON object. "
                    "You MUST respond with ONLY a raw JSON object starting with { and ending with }. "
                    "No prose, no markdown, no explanation — just the JSON object."
                )
                retry_prompt = prompt + json_nudge
                retry_temperature = max(0.0, temperature - 0.1)
                try:
                    raw_text_retry, _ = self.raw_generate(
                        role=role,
                        prompt=retry_prompt,
                        temperature=retry_temperature,
                        max_tokens=max_tokens,
                    )
                except Exception:
                    if trace is not None:
                        trace.record(
                            role=role,
                            provider=self.provider_name,
                            model=self.model_name,
                            ms=timer.elapsed_ms,
                            valid_first_try=False,
                        )
                    raise

                try:
                    if schema_cls is GenerateResult:
                        parsed = parse_generate_result(raw_text_retry)  # type: ignore
                    elif schema_cls is ProseResult:
                        parsed = parse_prose_result(raw_text_retry)  # type: ignore
                    else:
                        json_str = extract_json(raw_text_retry)
                        parsed = schema_cls.model_validate_json(json_str)  # type: ignore
                except (ValueError, Exception) as second_err:
                    if trace is not None:
                        trace.record(
                            role=role,
                            provider=self.provider_name,
                            model=self.model_name,
                            ms=timer.elapsed_ms,
                            valid_first_try=False,
                        )
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
        max_tokens: int = 4096,
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
        max_tokens: int = 4096,
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
