---
name: semantic-layer
description: Semantic layer specification, YAML schema from Section 9 of the handbook, and rules for dynamic schema loading without hardcoding table or column names in Python.
---

# Semantic Layer & Dynamic Schema Skill

This skill defines the schema format, conventions, and dynamic loading requirements for dataset configurations in DataMind Receipts.

## Strict Architectural Rule: No Hardcoded Schema Names

> **STRICT RULE:** No table or column names may ever be hardcoded in Python code or prompts.
> All table names, column names, descriptions, data types, synonyms, and join definitions must be loaded dynamically from the dataset YAML configuration at runtime.

### Why This Rule Exists:
1. **Config-only dataset switching:** The application must be able to switch from OWID to TPC-H (or other datasets) purely by pointing `dataset:` in `config.yaml` to another YAML file, without touching Python code.
2. **Dynamic few-shot retrieval and prompt construction:** Gemma's prompts must be constructed dynamically from the parsed YAML schema and pruned based on token budget.
3. **Generalization:** Prevents special-casing of queries or schema structures.

---

## Semantic Layer YAML Schema (Section 9)

Dataset files live in `datasets/<name>.yaml` (e.g. `datasets/owid.yaml`, `datasets/tpch.yaml`).

### Full YAML Specification

```yaml
# datasets/owid.yaml
name: owid
snowflake:
  database: DATAMIND
  schema: OWID

tables:
  life_expectancy:
    description: Life expectancy at birth by country and year
    columns:
      entity:
        type: text
        description: Country or region name
        synonyms: [country]
      code:
        type: text
        description: ISO3 code, null for regions
      year:
        type: int
        description: Calendar year
      life_expectancy:
        type: float
        description: Years
        sample_values: [72.6, 81.3]

# Start with one fully defined table. Add another indicator only after its real
# table and columns are loaded and tested. Do not declare joins to undefined tables.
joins: []
# Example join syntax when multiple tables exist:
# joins:
#   - from_table: life_expectancy
#     to_table: gdp_per_capita
#     on: "life_expectancy.entity = gdp_per_capita.entity AND life_expectancy.year = gdp_per_capita.year"

notes: []  # Add only dataset-specific caveats verified from the source metadata.

examples:
  - q: "Which 5 countries had the highest life expectancy in 2019?"
    sql: "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019 ORDER BY life_expectancy DESC LIMIT 5"

demo_mutation:      # optional; absent for read-only datasets such as the shared TPC-H sample
  description: "Correct one country-year value so the demo question's answer changes"
  # pick the country and year in hour 6 from the real data: the row must change the top 5 of the demo question
  sql: "UPDATE life_expectancy SET life_expectancy = <ABSOLUTE_NEW_VALUE> WHERE entity = '<COUNTRY>' AND year = <YEAR>"
  # absolute values, so clicking twice cannot drift; the load script makes life_expectancy_backup with CREATE TABLE ... CLONE
  reset_sql: "UPDATE life_expectancy t SET life_expectancy = b.life_expectancy FROM life_expectancy_backup b WHERE t.entity = b.entity AND t.year = b.year AND t.entity = '<COUNTRY>' AND t.year = <YEAR>"
```

### Global Configuration (`config.yaml`)

```yaml
provider: api            # api | ollama
model: gemma-...         # exact name from your AI Studio list; prefer a hosted Gemma model confirmed in the official API docs and available to your key
local_model: gemma-...   # exact name from `ollama list`, used when provider: ollama
temperature:
  sql: 0.1
  repair: 0.1
  prose: 0.2
  second_opinion: 0.7
prompt_token_budget: 3000   # prune the schema to fit; matters most for the local model
rows_to_gemma: 50           # most result rows sent to the prose call
dataset: datasets/owid.yaml
limits:
  max_rows: 200
  max_retries: 2
  query_timeout_s: 30
cache_llm_responses: false # keep false for demo/public mode; cached answers must never bypass live Snowflake queries
self_check: false        # second-SQL consistency check; enable if quota allows
public_mode: false       # true for any hosted demo: fixed dataset, switch and mutate endpoints off, rate limits on
snowsight_base_url: ""   # optional; if set, the receipt links the query ID to your Snowsight query history (only you can open it)
```

---

## Pydantic Model Pattern for Schema Validation

All YAML files must be parsed into Pydantic models to guarantee strict schema validation:

```python
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any

class ColumnDef(BaseModel):
    type: str
    description: str
    synonyms: List[str] = Field(default_factory=list)
    sample_values: Optional[List[Any]] = None

class TableDef(BaseModel):
    description: str
    columns: Dict[str, ColumnDef]

class JoinDef(BaseModel):
    from_table: str
    to_table: str
    on: str

class FewShotExample(BaseModel):
    q: str
    sql: str

class DemoMutation(BaseModel):
    description: str
    sql: str
    reset_sql: str

class SnowflakeConfig(BaseModel):
    database: str
    schema_name: str = Field(alias="schema")

class DatasetSchema(BaseModel):
    name: str
    snowflake: SnowflakeConfig
    tables: Dict[str, TableDef]
    joins: List[JoinDef] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    examples: List[FewShotExample] = Field(default_factory=list)
    demo_mutation: Optional[DemoMutation] = None
```

---

## Best Practices for Python Implementation

1. **Table allowlists:**
   Extract allowed tables using `dataset.tables.keys()`. Never hardcode `"life_expectancy"` or `"tpch"`.
2. **Prompt generation:**
   Construct schema representation by iterating over `dataset.tables.items()`, printing each column name, type, description, and synonyms.
3. **Pruning for Token Budget:**
   If the full schema exceeds `prompt_token_budget`, dynamically prune unreferenced tables or columns based on keyword relevance to the question (`src/schema_prune.py`).
4. **Receipt Generation:**
   Receipt components in `src/receipt.py` inspect the parsed `sqlglot` AST and resolve table aliases dynamically against `dataset.tables`.
