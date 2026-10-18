"""
Configuration and Semantic Layer dynamic schema loading.

Loads dataset YAML files (e.g. datasets/owid.yaml, datasets/tpch.yaml) and
the application config (config.yaml) using Pydantic models.

Strict architectural rule (AGENTS.md, Section 4, 9):
    No hardcoded table or column names in Python code.
    All schema information is loaded dynamically from dataset YAML files.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Load .env if present
try:
    from dotenv import load_dotenv

    _project_root = Path(__file__).resolve().parent.parent
    _env_file = _project_root / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
except ImportError:
    pass


# ── Semantic Layer Schema Models (Section 9) ─────────────────────────────────

class ColumnDef(BaseModel):
    """Definition of a single column in a dataset table."""
    model_config = ConfigDict(populate_by_name=True)

    type: str
    description: str
    synonyms: List[str] = Field(default_factory=list)
    sample_values: Optional[List[Any]] = None


class TableDef(BaseModel):
    """Definition of a table containing columns and table-level description."""
    model_config = ConfigDict(populate_by_name=True)

    description: str
    columns: Dict[str, ColumnDef]


class JoinDef(BaseModel):
    """Specification of a join between two tables in the dataset."""
    model_config = ConfigDict(populate_by_name=True)

    from_table: str
    to_table: str
    on: str

    @model_validator(mode="before")
    @classmethod
    def _normalize_yaml_keys(cls, data: Any) -> Any:
        """Handle YAML 1.1 quirk where unquoted 'on' is parsed as boolean True."""
        if isinstance(data, dict) and True in data and "on" not in data:
            data["on"] = data.pop(True)
        return data


class FewShotExample(BaseModel):
    """Natural-language question and corresponding validated ground-truth SQL."""
    model_config = ConfigDict(populate_by_name=True)

    q: str
    sql: str


class DemoMutation(BaseModel):
    """Mutation query and reset query for the local operator data-change demo."""
    model_config = ConfigDict(populate_by_name=True)

    description: str
    sql: str
    reset_sql: str


class SnowflakeConfig(BaseModel):
    """Snowflake target database and schema for a dataset."""
    model_config = ConfigDict(populate_by_name=True)

    database: str
    schema_name: str = Field(alias="schema")


class DatasetSchema(BaseModel):
    """Full semantic layer definition parsed from a dataset YAML file."""
    model_config = ConfigDict(populate_by_name=True)

    name: str
    snowflake: SnowflakeConfig
    tables: Dict[str, TableDef]
    joins: List[JoinDef] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    examples: List[FewShotExample] = Field(default_factory=list)
    demo_mutation: Optional[DemoMutation] = None

    @property
    def allowed_tables(self) -> List[str]:
        """Return list of allowed table names for SQL validation."""
        return list(self.tables.keys())


# ── Load dataset helper ───────────────────────────────────────────────────────

def load_dataset(path: str | Path) -> DatasetSchema:
    """Load and validate a dataset semantic layer YAML file."""
    path_obj = Path(path)
    if not path_obj.is_file():
        raise FileNotFoundError(f"Dataset file not found: {path}")

    with open(path_obj, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML structure in {path}: expected a dictionary")

    return DatasetSchema.model_validate(data)


# ── Global App Config Models (Section 9) ────────────────────────────────────

class TemperatureConfig(BaseModel):
    sql: float = 0.1
    repair: float = 0.1
    prose: float = 0.2
    second_opinion: float = 0.7


class LimitsConfig(BaseModel):
    max_rows: int = 200
    max_retries: int = 2
    query_timeout_s: int = 30
    max_tokens_sql: int = 8192
    max_tokens_prose: int = 512


class AppConfig(BaseModel):
    """
    Validated application configuration loaded from config.yaml.

    No secrets are stored here.  API keys come exclusively from environment
    variables accessed via get_api_key().
    """
    model_config = ConfigDict(populate_by_name=True)

    provider: Literal["api", "ollama"] = "api"
    model: str = "gemma-4-31b-it"
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
        if "gemma" not in v.lower():
            raise ValueError(
                f"config.yaml: model '{v}' does not appear to identify a Gemma "
                "model variant (expected the string 'gemma' to appear in the "
                "model identifier)."
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


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """Load and validate configuration from a YAML file."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_path.resolve()}\n"
            "Copy config.yaml (the template) to this path and fill in required fields."
        )

    with config_path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    forbidden_keys = {
        "api_key", "apikey", "google_api_key", "google_gemma_api_key",
        "password", "secret", "token",
    }
    found = {k for k in raw if k.lower() in forbidden_keys}
    if found:
        raise ValueError(
            f"config.yaml must not contain secrets. Remove these keys: {found}. "
            "Put credentials in environment variables (see .env.example)."
        )

    return AppConfig.model_validate(raw)


_API_KEY_ENV_VAR = "GOOGLE_GEMMA_API_KEY"


def get_api_key() -> str:
    """Read the Google AI Studio API key from the environment."""
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
