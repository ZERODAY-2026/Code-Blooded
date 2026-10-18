"""
src/config.py — Configuration loader for DataMind Receipts.

Rules enforced here
───────────────────
- API keys are read exclusively from environment variables.  They are never
  stored in AppConfig fields, never logged, and never written to config.yaml.
- Model identifiers must be explicitly configured.  The app raises an error
  rather than silently falling back to a different model or provider.
- The Gemma model string check (must contain "gemma") is a config-level guard
  only.  It does NOT prove what model the API actually serves at runtime.
  Runtime model identity should be recorded from provider response metadata
  where available (implemented in the provider layer, M2).
- Execution-time resource limits (warehouse size, statement timeout, credit
  monitor) are enforced by Person A in Snowflake and src/execute.py.
  The limits in AppConfig are application-layer guardrails only.

Usage
─────
    from src.config import load_config, get_api_key

    cfg = load_config()          # reads config.yaml by default
    key = get_api_key()          # reads GOOGLE_GEMMA_API_KEY from env
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# Load .env if present
try:
    from dotenv import load_dotenv

    _project_root = Path(__file__).resolve().parent.parent
    _env_file = _project_root / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
except ImportError:
    pass


# ── Sub-models ────────────────────────────────────────────────────────────────

class TemperatureConfig(BaseModel):
    sql: float = 0.1
    repair: float = 0.1
    prose: float = 0.2
    second_opinion: float = 0.7


class LimitsConfig(BaseModel):
    # Application-layer row cap enforced in pipeline.py.
    max_rows: int = 200
    # Maximum SQL repair attempts; after this the pipeline returns a structured failure.
    max_retries: int = 2
    # Passed to execute.py as a hint.  Actual timeout is enforced in Snowflake.
    query_timeout_s: int = 30
    # Max output tokens for SQL generation / repair roles.
    # gemma-4-26b-a4b-it uses chain-of-thought internally and needs ~8 k tokens
    # to complete its reasoning and emit the final JSON before being truncated.
    max_tokens_sql: int = 8192
    # Max output tokens for the prose (explain) role.  Plain prose is short.
    max_tokens_prose: int = 512


# ── Main config model ─────────────────────────────────────────────────────────

class AppConfig(BaseModel):
    """
    Validated application configuration loaded from config.yaml.

    No secrets are stored here.  API keys come exclusively from environment
    variables accessed via get_api_key().
    """

    provider: Literal["api", "ollama"]

    # Exact model identifier; must be set explicitly.
    model: str

    # Required when provider=ollama; must be set to a tested local tag.
    local_model: str = ""

    temperature: TemperatureConfig = Field(default_factory=TemperatureConfig)
    prompt_token_budget: int = 3000
    rows_to_gemma: int = 50
    dataset: str = "datasets/owid.yaml"
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    cache_llm_responses: bool = False
    self_check: bool = False
    public_mode: bool = False
    snowsight_base_url: str = ""

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("model")
    @classmethod
    def model_must_be_set(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "config.yaml: 'model' must be set to an explicit model identifier. "
                "Look up available Gemma model IDs in your Google AI Studio console "
                "and write the exact string here."
            )
        return v.strip()

    @field_validator("model")
    @classmethod
    def model_must_identify_gemma(cls, v: str) -> str:
        """
        Config-level guard: the configured model string must contain 'gemma'.

        This check validates the configuration, not runtime model identity.
        It prevents accidentally pointing the app at a non-Gemma model (e.g.
        gemini-pro, gpt-4) without realising it.  It does NOT guarantee that
        the API serves a Gemma model; the provider layer (M2) should record
        response metadata for runtime tracing.
        """
        if "gemma" not in v.lower():
            raise ValueError(
                f"config.yaml: model '{v}' does not appear to identify a Gemma "
                "model variant (expected the string 'gemma' to appear in the "
                "model identifier).  If you believe this is a valid Gemma model, "
                "update this check in src/config.py and document why."
            )
        return v

    @model_validator(mode="after")
    def ollama_requires_local_model(self) -> "AppConfig":
        if self.provider == "ollama" and not self.local_model.strip():
            raise ValueError(
                "config.yaml: 'local_model' must be set when provider is 'ollama'. "
                "Run `ollama list` to find an available Gemma tag and set it."
            )
        return self

    @field_validator("prompt_token_budget")
    @classmethod
    def token_budget_positive(cls, v: int) -> int:
        if v < 256:
            raise ValueError("prompt_token_budget must be at least 256 tokens.")
        return v

    @field_validator("rows_to_gemma")
    @classmethod
    def rows_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("rows_to_gemma must be at least 1.")
        return v


# ── Loader ────────────────────────────────────────────────────────────────────

def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """
    Load and validate configuration from a YAML file.

    Raises
    ──────
    FileNotFoundError  — config file does not exist at the given path.
    ValueError         — one or more config values fail validation.
    yaml.YAMLError     — the file is not valid YAML.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_path.resolve()}\n"
            "Copy config.yaml (the template) to this path and fill in required fields."
        )

    with config_path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    # Guard: reject any attempt to embed an API key in the config file.
    forbidden_keys = {"api_key", "apikey", "google_api_key", "google_gemma_api_key",
                      "password", "secret", "token"}
    found = {k for k in raw if k.lower() in forbidden_keys}
    if found:
        raise ValueError(
            f"config.yaml must not contain secrets. Remove these keys: {found}. "
            "Put credentials in environment variables (see .env.example)."
        )

    return AppConfig.model_validate(raw)


# ── API key access ────────────────────────────────────────────────────────────

_API_KEY_ENV_VAR = "GOOGLE_GEMMA_API_KEY"


def get_api_key() -> str:
    """
    Read the Google AI Studio API key from the environment.

    Returns the key string.  Raises EnvironmentError if the variable is absent
    or empty.  The key is never stored in module-level state, never logged, and
    never returned in any API response.
    """
    key = os.environ.get(_API_KEY_ENV_VAR, "").strip()
    if not key:
        raise EnvironmentError(
            f"Environment variable {_API_KEY_ENV_VAR!r} is not set or is empty. "
            "Set it in your local .env file (never in config.yaml or source code). "
            "See .env.example for instructions."
        )
    return key


def api_key_env_var_name() -> str:
    """Return the name of the API key environment variable (for documentation only)."""
    return _API_KEY_ENV_VAR
