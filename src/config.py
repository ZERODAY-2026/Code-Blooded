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
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class AppConfig(BaseModel):
    """Application-wide configuration loaded from config.yaml."""
    model_config = ConfigDict(populate_by_name=True)

    provider: str = "api"  # "api" or "ollama"
    model: str = "gemma-4-31b-it"
    local_model: str = "gemma-2-9b-it"
    temperature: TemperatureConfig = Field(default_factory=TemperatureConfig)
    prompt_token_budget: int = 3000
    rows_to_gemma: int = 50
    dataset: str = "datasets/owid.yaml"
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    cache_llm_responses: bool = False
    self_check: bool = False
    public_mode: bool = False
    snowsight_base_url: str = ""


# ── Loaders ──────────────────────────────────────────────────────────────────

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


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """Load application configuration from config.yaml."""
    path_obj = Path(path)
    if not path_obj.is_file():
        # Return default config if file does not exist
        return AppConfig()

    with open(path_obj, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        return AppConfig()

    return AppConfig.model_validate(data)
