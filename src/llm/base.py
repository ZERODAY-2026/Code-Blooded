"""
src/llm/base.py — Abstract provider interface and shared error types.

Implementations (gemma_api.py, ollama.py) must subclass LLMProvider and
satisfy the contract documented here.  The pipeline imports only this ABC,
which keeps providers swappable without touching pipeline code.

No network calls, no file I/O, no Snowflake in this module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


# ── Provider errors ───────────────────────────────────────────────────────────

class ProviderError(Exception):
    """
    Raised when the LLM provider encounters an unrecoverable error.

    Use is_rate_limit=True to signal that a bounded backoff-and-retry
    is appropriate at the call site rather than an immediate failure.
    """

    def __init__(self, message: str, *, is_rate_limit: bool = False) -> None:
        # Do NOT include the full prompt or API response in the message;
        # it may contain schema text or partial user input.
        super().__init__(message)
        self.is_rate_limit = is_rate_limit


class ModelIdentityError(ValueError):
    """
    Raised when a provider is constructed with a model identifier that does
    not identify a Gemma model variant.

    This is a configuration error, not a runtime API error.
    """


# ── Abstract provider ─────────────────────────────────────────────────────────

class LLMProvider(ABC):
    """
    Abstract base class for all Gemma model providers.

    Design rules
    ────────────
    - Model names come from config, never hardcoded in implementations.
    - The provider records its configured identity (provider_name, model_name)
      in every trace entry.  It does NOT guarantee that the API serves the
      exact model requested — that depends on provider availability.
    - Implementations must raise ModelIdentityError at construction time if
      the configured model string does not identify a Gemma variant.
    - Implementations must never log the full prompt (it may contain schema
      text) or the API key.  Logging the role and approximate token count is
      acceptable at DEBUG level.
    - Implementations must never silently fall back to a different model or
      provider.  A ProviderError with a clear message is preferable to a
      silent degradation.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider type string: 'api' or 'ollama'."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """
        Return the exact configured model identifier string.

        This is what the config says to use.  It is recorded in gemma_calls
        traces.  It is NOT a guarantee of what the server actually ran.
        """

    @abstractmethod
    def raw_generate(
        self,
        role: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, bool]:
        """
        Send a prompt to the model and return the raw text response.

        Parameters
        ──────────
        role        — Logical role for tracing: 'generate' | 'repair' | 'explain'.
                      Logged at INFO level only; the prompt content is not logged.
        prompt      — Full prompt text.  Must not contain API keys or passwords.
        temperature — Sampling temperature from config.
        max_tokens  — Maximum output tokens from config per role.

        Returns
        ───────
        (raw_text, valid_on_first_try) where:
          raw_text          — The model's text output.
          valid_on_first_try — True if the request succeeded on the first HTTP
                              attempt (no rate-limit retry was needed).

        Raises
        ──────
        ProviderError(is_rate_limit=True)  — after exhausting backoff retries.
        ProviderError(is_rate_limit=False) — on timeout or unrecoverable error.
        """

    # ── Convenience helpers (non-abstract) ────────────────────────────────────

    def identity(self) -> dict[str, str]:
        """Return a dict suitable for the 'model' field in AskResponse."""
        return {"provider": self.provider_name, "name": self.model_name}

    @staticmethod
    def _assert_gemma_model(model_id: str) -> None:
        """
        Shared guard called by __init__ of concrete providers.

        Raises ModelIdentityError if the model string does not contain 'gemma'.
        See the class docstring for the caveat about runtime identity.
        """
        if not model_id or "gemma" not in model_id.lower():
            raise ModelIdentityError(
                f"Provider configured with model '{model_id}', which does not "
                "appear to identify a Gemma model variant.  Check config.yaml "
                "and set 'model' to a Gemma model identifier (e.g. gemma-3-27b-it). "
                "The app will not silently fall back to a different model family."
            )
